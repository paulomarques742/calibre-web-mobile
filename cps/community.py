# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#  calibre-web-mobile fork: social features (per-user ratings, reviews, likes).
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

from flask import Blueprint, request, jsonify
from flask_babel import gettext as _
from sqlalchemy.sql.expression import func

from . import ub, calibre_db, logger, config
from .cw_login import current_user
from .usermanagement import user_login_required

try:
    from bleach import clean as _bleach_clean
    _has_bleach = True
except ImportError:
    _has_bleach = False

log = logger.create()

community = Blueprint('community', __name__)

# Reviews accept only light inline formatting; everything else is stripped.
_REVIEW_TAGS = {"b", "i", "em", "strong", "p", "br", "ul", "ol", "li", "blockquote"}
_REVIEW_MAX = 5000


def _sanitize_review(text):
    text = (text or "").strip()[:_REVIEW_MAX]
    if not text:
        return ""
    if _has_bleach:
        return _bleach_clean(text, tags=_REVIEW_TAGS, attributes={}, strip=True)
    # Fallback: strip all markup if bleach is unavailable.
    import re
    return re.sub(r"<[^>]*>", "", text)


def _is_real_user():
    return current_user.is_authenticated and not current_user.is_anonymous


def get_community_block(book_id):
    """Assemble the social data shown on a book's detail page."""
    uid = current_user.id if _is_real_user() else None

    ratings = ub.session.query(ub.BookRating).filter(ub.BookRating.book_id == book_id).all()
    rating_count = len(ratings)
    rating_avg = round(sum(r.rating for r in ratings) / rating_count, 1) if rating_count else 0
    your_rating = next((r.rating for r in ratings if r.user_id == uid), 0)

    like_rows = ub.session.query(ub.BookLike).filter(ub.BookLike.book_id == book_id).all()
    like_count = len(like_rows)
    you_liked = any(l.user_id == uid for l in like_rows)

    # Map user ids -> names in one query for reviews + ratings display.
    review_rows = ub.session.query(ub.BookReview).filter(ub.BookReview.book_id == book_id) \
        .order_by(ub.BookReview.last_modified.desc()).all()
    user_ids = {r.user_id for r in review_rows}
    names = {}
    if user_ids:
        for u in ub.session.query(ub.User.id, ub.User.name).filter(ub.User.id.in_(user_ids)).all():
            names[u.id] = u.name
    rating_by_user = {r.user_id: r.rating for r in ratings}

    # Review like counts in one grouped query.
    review_ids = [r.id for r in review_rows]
    rl_counts = {}
    rl_you = set()
    if review_ids:
        for rid, cnt in ub.session.query(ub.ReviewLike.review_id, func.count(ub.ReviewLike.id)) \
                .filter(ub.ReviewLike.review_id.in_(review_ids)) \
                .group_by(ub.ReviewLike.review_id).all():
            rl_counts[rid] = cnt
        if uid:
            rl_you = {r.review_id for r in ub.session.query(ub.ReviewLike.review_id)
                      .filter(ub.ReviewLike.review_id.in_(review_ids), ub.ReviewLike.user_id == uid).all()}

    reviews = []
    your_review = ""
    for r in review_rows:
        if r.user_id == uid:
            your_review = r.text
        reviews.append({
            "id": r.id,
            "user": names.get(r.user_id, _("Unknown")),
            "is_you": r.user_id == uid,
            "text": r.text,
            "rating": rating_by_user.get(r.user_id, 0),
            "date": r.last_modified,
            "likes": rl_counts.get(r.id, 0),
            "you_liked": r.id in rl_you,
        })

    return {
        "book_id": book_id,
        "can_interact": uid is not None,
        "rating_avg": rating_avg,
        "rating_count": rating_count,
        "your_rating": your_rating,
        "like_count": like_count,
        "you_liked": you_liked,
        "reviews": reviews,
        "your_review": your_review,
        "downloaders": _get_downloaders(book_id),
    }


def _get_downloaders(book_id):
    """Names of users who downloaded this book. Respects the admin master
    switch and each user's privacy opt-out; admins always see everyone."""
    is_admin = current_user.is_authenticated and current_user.role_admin()
    if not is_admin and not getattr(config, "config_show_download_activity", True):
        return []
    q = ub.session.query(ub.User.name).join(ub.Downloads, ub.Downloads.user_id == ub.User.id) \
        .filter(ub.Downloads.book_id == book_id)
    if not is_admin:
        q = q.filter((ub.User.privacy_hide_activity == False) | (ub.User.privacy_hide_activity.is_(None)))  # noqa: E712
    return [row[0] for row in q.all()]


def _require_book(book_id):
    return calibre_db.get_book(book_id) is not None


@community.route("/ajax/rating/<int:book_id>", methods=["POST"])
@user_login_required
def set_rating(book_id):
    if not _is_real_user():
        return jsonify(error=_("Login required")), 403
    if not _require_book(book_id):
        return jsonify(error=_("Book not found")), 404
    try:
        value = int((request.get_json(silent=True) or {}).get("rating", 0))
    except (TypeError, ValueError):
        return jsonify(error=_("Invalid rating")), 400
    if value < 0 or value > 5:
        return jsonify(error=_("Invalid rating")), 400

    row = ub.session.query(ub.BookRating).filter(
        ub.BookRating.book_id == book_id, ub.BookRating.user_id == current_user.id).first()
    if value == 0:
        if row:
            ub.session.delete(row)
    elif row:
        row.rating = value
    else:
        ub.session.add(ub.BookRating(book_id=book_id, user_id=current_user.id, rating=value))
    ub.session_commit()
    _invalidate_recs()

    ratings = ub.session.query(ub.BookRating).filter(ub.BookRating.book_id == book_id).all()
    count = len(ratings)
    avg = round(sum(r.rating for r in ratings) / count, 1) if count else 0
    return jsonify(avg=avg, count=count, your=value)


@community.route("/ajax/review/<int:book_id>", methods=["POST"])
@user_login_required
def set_review(book_id):
    if not _is_real_user():
        return jsonify(error=_("Login required")), 403
    if not _require_book(book_id):
        return jsonify(error=_("Book not found")), 404
    text = _sanitize_review((request.get_json(silent=True) or {}).get("text", ""))
    row = ub.session.query(ub.BookReview).filter(
        ub.BookReview.book_id == book_id, ub.BookReview.user_id == current_user.id).first()
    if not text:
        if row:
            ub.session.query(ub.ReviewLike).filter(ub.ReviewLike.review_id == row.id).delete()
            ub.session.delete(row)
            ub.session_commit()
        return jsonify(deleted=True)
    if row:
        row.text = text
    else:
        ub.session.add(ub.BookReview(book_id=book_id, user_id=current_user.id, text=text))
    ub.session_commit()
    return jsonify(ok=True, text=text)


@community.route("/ajax/review/<int:review_id>/delete", methods=["POST"])
@user_login_required
def delete_review(review_id):
    if not _is_real_user():
        return jsonify(error=_("Login required")), 403
    row = ub.session.query(ub.BookReview).filter(ub.BookReview.id == review_id).first()
    if not row:
        return jsonify(deleted=True)
    if row.user_id != current_user.id and not current_user.role_admin():
        return jsonify(error=_("Not allowed")), 403
    ub.session.query(ub.ReviewLike).filter(ub.ReviewLike.review_id == review_id).delete()
    ub.session.delete(row)
    ub.session_commit()
    return jsonify(deleted=True)


@community.route("/ajax/like/book/<int:book_id>", methods=["POST"])
@user_login_required
def like_book(book_id):
    if not _is_real_user():
        return jsonify(error=_("Login required")), 403
    if not _require_book(book_id):
        return jsonify(error=_("Book not found")), 404
    row = ub.session.query(ub.BookLike).filter(
        ub.BookLike.book_id == book_id, ub.BookLike.user_id == current_user.id).first()
    if row:
        ub.session.delete(row)
        liked = False
    else:
        ub.session.add(ub.BookLike(book_id=book_id, user_id=current_user.id))
        liked = True
    ub.session_commit()
    _invalidate_recs()
    count = ub.session.query(func.count(ub.BookLike.id)).filter(ub.BookLike.book_id == book_id).scalar()
    return jsonify(liked=liked, count=count)


@community.route("/ajax/like/review/<int:review_id>", methods=["POST"])
@user_login_required
def like_review(review_id):
    if not _is_real_user():
        return jsonify(error=_("Login required")), 403
    if not ub.session.query(ub.BookReview.id).filter(ub.BookReview.id == review_id).first():
        return jsonify(error=_("Review not found")), 404
    row = ub.session.query(ub.ReviewLike).filter(
        ub.ReviewLike.review_id == review_id, ub.ReviewLike.user_id == current_user.id).first()
    if row:
        ub.session.delete(row)
        liked = False
    else:
        ub.session.add(ub.ReviewLike(review_id=review_id, user_id=current_user.id))
        liked = True
    ub.session_commit()
    count = ub.session.query(func.count(ub.ReviewLike.id)).filter(ub.ReviewLike.review_id == review_id).scalar()
    return jsonify(liked=liked, count=count)


def _invalidate_recs():
    # Recommendations (Phase 6) cache invalidation; no-op until that module lands.
    try:
        from . import recommendations
        recommendations.invalidate(current_user.id)
    except Exception:
        pass
