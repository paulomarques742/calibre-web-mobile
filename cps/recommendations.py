# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#  calibre-web-mobile fork: content-based book recommendations ("Discover for you").
#
#  Pure SQL + Python, no extra dependencies. Signals come from app.db (ratings,
#  likes, read status, downloads); the book/tag/author/series graph comes from
#  calibre's metadata.db via calibre_db. Results are cached per user for a while
#  and invalidated when the user's signals change.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

import math
import time
import threading

from sqlalchemy.sql.expression import func

from . import calibre_db, db, ub, logger

log = logger.create()

_CACHE_TTL = 1800  # seconds
_cache = {}
_cache_lock = threading.Lock()

# Weight of each signal when scoring the *seed* books a user liked/read.
_W_RATING = {5: 3.0, 4: 2.0, 3: 1.0, 2: -2.0, 1: -2.0}
_W_LIKE = 2.0
_W_FINISHED = 2.0
_W_IN_PROGRESS = 1.0
_W_DOWNLOAD = 1.0
_SEED_CAP = 5.0

# Field weights when scoring candidate books against the taste profile.
_TAG_WEIGHT = 1.0
_AUTHOR_WEIGHT = 2.0
_SERIES_WEIGHT = 3.0
_FRIENDS_WEIGHT = 0.3

_TOP_PROFILE = 20  # how many top tags/authors/series to prefilter candidates on


def invalidate(user_id):
    with _cache_lock:
        _cache.pop(user_id, None)


def _seed_weights(user_id):
    """book_id -> accumulated seed weight from this user's signals."""
    weights = {}

    def add(book_id, w):
        weights[book_id] = weights.get(book_id, 0.0) + w

    for r in ub.session.query(ub.BookRating).filter(ub.BookRating.user_id == user_id).all():
        add(r.book_id, _W_RATING.get(r.rating, 0.0))
    for lk in ub.session.query(ub.BookLike).filter(ub.BookLike.user_id == user_id).all():
        add(lk.book_id, _W_LIKE)
    for rb in ub.session.query(ub.ReadBook).filter(ub.ReadBook.user_id == user_id).all():
        if rb.read_status == ub.ReadBook.STATUS_FINISHED:
            add(rb.book_id, _W_FINISHED)
        elif rb.read_status == ub.ReadBook.STATUS_IN_PROGRESS:
            add(rb.book_id, _W_IN_PROGRESS)
    for dl in ub.session.query(ub.Downloads).filter(ub.Downloads.user_id == user_id).all():
        add(dl.book_id, _W_DOWNLOAD)

    # Cap and drop non-positive seeds (a disliked book shouldn't pull recs toward it).
    return {b: min(w, _SEED_CAP) for b, w in weights.items() if w > 0}


def _links_for_books(link_table, key_col, book_ids):
    """Return list of (book_id, key_id) rows from a calibre link table."""
    if not book_ids:
        return []
    col = link_table.c[key_col]
    rows = calibre_db.session.query(link_table.c.book, col) \
        .filter(link_table.c.book.in_(book_ids)).all()
    return [(r[0], r[1]) for r in rows]


def _global_counts(link_table, key_col):
    """key_id -> number of books carrying it (for damping ubiquitous tags)."""
    col = link_table.c[key_col]
    rows = calibre_db.session.query(col, func.count(link_table.c.book)).group_by(col).all()
    return {r[0]: r[1] for r in rows}


def _build_profile(seed_weights):
    """Accumulate weighted tag/author/series preferences from seed books,
    damping by global frequency so common tags don't dominate."""
    seed_ids = list(seed_weights.keys())
    profile = {"tag": {}, "author": {}, "series": {}}
    sources = (
        ("tag", db.books_tags_link, "tag"),
        ("author", db.books_authors_link, "author"),
        ("series", db.books_series_link, "series"),
    )
    for kind, table, col in sources:
        counts = _global_counts(table, col)
        acc = profile[kind]
        for book_id, key_id in _links_for_books(table, col, seed_ids):
            damp = 1.0 / math.log(2 + counts.get(key_id, 1))
            acc[key_id] = acc.get(key_id, 0.0) + seed_weights[book_id] * damp
    return profile


def _top_keys(weight_map, n):
    return sorted(weight_map, key=weight_map.get, reverse=True)[:n]


def _candidate_ids(profile, exclude_ids):
    """Books sharing at least one top tag/author/series with the profile,
    excluding already-seen books."""
    cand = set()
    sources = (
        (db.books_tags_link, "tag", profile["tag"]),
        (db.books_authors_link, "author", profile["author"]),
        (db.books_series_link, "series", profile["series"]),
    )
    for table, col, weights in sources:
        keys = _top_keys(weights, _TOP_PROFILE)
        if not keys:
            continue
        rows = calibre_db.session.query(table.c.book) \
            .filter(table.c[col].in_(keys)).all()
        cand.update(r[0] for r in rows)
    return cand - exclude_ids


def _friends_avg():
    """book_id -> average of all users' ratings (small collaborative nudge)."""
    rows = ub.session.query(ub.BookRating.book_id, func.avg(ub.BookRating.rating)) \
        .group_by(ub.BookRating.book_id).all()
    return {r[0]: float(r[1]) for r in rows}


def _score_candidates(profile, candidate_ids):
    cand = list(candidate_ids)
    scores = {c: 0.0 for c in cand}
    field_specs = (
        (db.books_tags_link, "tag", profile["tag"], _TAG_WEIGHT),
        (db.books_authors_link, "author", profile["author"], _AUTHOR_WEIGHT),
        (db.books_series_link, "series", profile["series"], _SERIES_WEIGHT),
    )
    for table, col, weights, field_weight in field_specs:
        for book_id, key_id in _links_for_books(table, col, cand):
            w = weights.get(key_id)
            if w:
                scores[book_id] += w * field_weight

    friends = _friends_avg()
    for c in cand:
        if c in friends:
            scores[c] += _FRIENDS_WEIGHT * friends[c]
    return scores


def _excluded_book_ids(user_id):
    """Books the user has already engaged with -> never recommend."""
    ex = set()
    ex.update(r[0] for r in ub.session.query(ub.Downloads.book_id)
              .filter(ub.Downloads.user_id == user_id).all())
    ex.update(r[0] for r in ub.session.query(ub.ReadBook.book_id)
              .filter(ub.ReadBook.user_id == user_id,
                      ub.ReadBook.read_status == ub.ReadBook.STATUS_FINISHED).all())
    ex.update(r[0] for r in ub.session.query(ub.ArchivedBook.book_id)
              .filter(ub.ArchivedBook.user_id == user_id, ub.ArchivedBook.is_archived == True).all())  # noqa: E712
    return ex


def _popular_fallback(exclude_ids, limit):
    """Cold-start: most downloaded + liked books the user hasn't seen."""
    counts = {}
    for book_id, cnt in ub.session.query(ub.Downloads.book_id, func.count(ub.Downloads.id)) \
            .group_by(ub.Downloads.book_id).all():
        counts[book_id] = counts.get(book_id, 0) + cnt
    for book_id, cnt in ub.session.query(ub.BookLike.book_id, func.count(ub.BookLike.id)) \
            .group_by(ub.BookLike.book_id).all():
        counts[book_id] = counts.get(book_id, 0) + cnt * 2
    ranked = [b for b in sorted(counts, key=counts.get, reverse=True) if b not in exclude_ids]
    return ranked[:limit]


def _compute(user_id, limit):
    try:
        seed_weights = _seed_weights(user_id)
        exclude = _excluded_book_ids(user_id)
        if not seed_weights:
            return _popular_fallback(exclude, limit)

        profile = _build_profile(seed_weights)
        exclude = exclude | set(seed_weights.keys())
        candidates = _candidate_ids(profile, exclude)
        if not candidates:
            return _popular_fallback(exclude, limit)

        scores = _score_candidates(profile, candidates)
        ranked = [b for b in sorted(scores, key=scores.get, reverse=True) if scores[b] > 0]
        if not ranked:
            return _popular_fallback(exclude, limit)
        return ranked[:limit]
    except Exception as ex:
        log.warning("recommendation computation failed for user %s: %s", user_id, ex)
        return []


def get_recommended_ids(user_id, limit=24):
    """Cached list of recommended calibre book ids for a user."""
    now = time.time()
    with _cache_lock:
        entry = _cache.get(user_id)
        if entry and entry[0] > now:
            return entry[1][:limit]
    ids = _compute(user_id, max(limit, 24))
    with _cache_lock:
        _cache[user_id] = (now + _CACHE_TTL, ids)
    return ids[:limit]


def get_recommended_books(user_id, limit=24):
    """Hydrate recommended ids into Books objects, preserving rank order and
    applying the standard visibility filters."""
    ids = get_recommended_ids(user_id, limit)
    if not ids:
        return []
    books = calibre_db.session.query(db.Books).filter(db.Books.id.in_(ids)) \
        .filter(calibre_db.common_filters(True)).all()
    order = {bid: i for i, bid in enumerate(ids)}
    books.sort(key=lambda b: order.get(b.id, 10 ** 9))
    return books
