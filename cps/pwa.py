# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#  calibre-web-mobile fork: Progressive Web App support (manifest + service worker).
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

import json

from flask import Blueprint, Response, render_template, url_for

from . import config, constants

pwa = Blueprint('pwa', __name__)


def _theme_colors():
    # caliBlur (theme 1) is dark; default theme is light.
    if getattr(config, 'config_theme', 0) == 1:
        return "#1f2124", "#1f2124"
    return "#ffffff", "#45b29d"


@pwa.route("/manifest.json")
def manifest():
    background_color, theme_color = _theme_colors()
    title = config.config_calibre_web_title or "Calibre-Web"
    data = {
        "name": title,
        "short_name": title[:12],
        "description": "Your personal book library",
        "start_url": url_for('web.index'),
        "scope": url_for('web.index'),
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": background_color,
        "theme_color": theme_color,
        "icons": [
            {"src": url_for('static', filename='icons/icon-192.png'),
             "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": url_for('static', filename='icons/icon-512.png'),
             "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": url_for('static', filename='icons/icon-maskable-512.png'),
             "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return Response(
        json.dumps(data),
        mimetype="application/manifest+json",
    )


@pwa.route("/sw.js")
def service_worker():
    body = render_template(
        "sw.js",
        cache_version="cwm-{}-{}".format(constants.STABLE_VERSION, constants.CWM_VERSION),
        static_base=url_for('static', filename='').rstrip('/'),
        offline_url=url_for('pwa.offline'),
        index_url=url_for('web.index'),
    )
    resp = Response(body, mimetype="application/javascript")
    # Never let intermediaries cache the SW itself.
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@pwa.route("/offline")
def offline():
    return render_template("offline.html", instance=config.config_calibre_web_title)
