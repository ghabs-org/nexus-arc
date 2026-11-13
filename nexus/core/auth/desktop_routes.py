"""Thin Flask adapter for the reusable desktop identity bridge."""

from functools import wraps
from html import escape
from urllib.parse import urlencode

from nexus.core.auth import desktop_login as login


def install_desktop_login_routes(app, *, enabled, session_cookie_name, render_page):
    """Use a host's existing auth cookie, login page and page renderer."""
    from flask import jsonify, make_response, redirect, request

    def endpoint(*, native=False):
        def decorate(function):
            def failure(message, status):
                if native:
                    return make_response(jsonify(error=message), status)
                markup = (
                    "<p>"
                    + escape(message)
                    + "</p><p>Return to Nexus Studio and start sign-in again.</p>"
                )
                return make_response(render_page("Desktop sign-in", markup), status)

            @wraps(function)
            def handler():
                if not enabled():
                    return jsonify(error="Authentication is disabled."), 404
                if native and request.headers.get("Origin") is not None:
                    return jsonify(error="Use the native Nexus account flow."), 403
                try:
                    response = make_response(function())
                except PermissionError as exc:
                    response = failure(str(exc), 401 if native else 403)
                except (ValueError, TypeError):
                    response = failure(
                        "Invalid or expired desktop sign-in. Start again in Studio.", 400
                    )
                except OverflowError:
                    response = failure("Too many pending sign-ins. Try again later.", 429)
                except Exception:
                    # Upstream errors and credentials must never enter browser/API output.
                    response = failure("Nexus account service is unavailable.", 503)
                response.headers["Cache-Control"] = "no-store"
                response.headers["Referrer-Policy"] = (
                    "no-referrer" if response.status_code == 303 else "same-origin"
                )
                response.headers["X-Frame-Options"] = "DENY"
                return response

            return handler

        return decorate

    def body():
        if not request.is_json or not request.content_length or request.content_length > 4096:
            raise ValueError("Invalid desktop request.")
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("Invalid desktop request.")
        return data

    @app.route("/auth/desktop/start", methods=["POST"])
    @endpoint(native=True)
    def desktop_start():
        return jsonify(login.start(**body()))

    @app.route("/auth/desktop/authorize", methods=["GET", "POST"])
    @endpoint()
    def desktop_authorize():
        attempt_id = str(request.values.get("attempt_id", ""))
        session_id = request.cookies.get(session_cookie_name, "")
        try:
            details = login.confirmation(attempt_id=attempt_id, session_id=session_id)
        except PermissionError:
            if request.method != "GET":
                raise
            return redirect("/login?" + urlencode({"next": login.authorization_path(attempt_id)}))
        if request.method == "GET":
            markup = (
                '<section class="setting"><div class="label"><h2>Connect this desktop</h2>'
                "<p>Your local workspace and subscription credentials stay on this computer.</p></div>"
                '<div class="panel"><p>Sign in to <strong>'
                + escape(details["device_name"])
                + "</strong> as "
                "<strong>" + escape(details["account"]) + "</strong>?</p>"
                "<p>This device can view your Nexus identity and existing project names. "
                "This does not grant new project access or synchronize files.</p>"
                "<p>The connection expires after 30 days. Sign out through the desktop "
                "Account menu to revoke it sooner.</p>"
                '<form method="post" action="/auth/desktop/authorize"><input type="hidden" name="attempt_id" value="'
                + escape(attempt_id, quote=True)
                + '"><input type="hidden" name="csrf" value="'
                + details["csrf"]
                + '"><div class="provider-actions"><button class="btn primary" name="decision" '
                'value="allow">Connect desktop</button><button class="btn" name="decision" value="deny">Cancel</button>'
                "</div></form></div></section>"
            )
            response = make_response(render_page("Connect Nexus Studio", markup))
            response.set_cookie(
                login.COOKIE_NAME,
                details["context"],
                max_age=login.ATTEMPT_TTL,
                path="/auth/desktop",
                secure=True,
                httponly=True,
                samesite="Strict",
            )
            return response
        if (
            request.headers.get("Origin") != login.public_origin()
            or request.headers.get("Sec-Fetch-Site") == "cross-site"
        ):
            raise PermissionError("Desktop approval must start on this Nexus site.")
        decision = request.form.get("decision")
        if decision not in {"allow", "deny"}:
            raise ValueError("Choose connect or cancel.")
        destination = login.approve(
            attempt_id=attempt_id,
            session_id=session_id,
            context=request.cookies.get(login.COOKIE_NAME, ""),
            csrf=request.form.get("csrf", ""),
            allow=decision == "allow",
        )
        response = redirect(destination, code=303)
        response.delete_cookie(
            login.COOKIE_NAME, path="/auth/desktop", secure=True, httponly=True, samesite="Strict"
        )
        return response

    @app.route("/auth/desktop/exchange", methods=["POST"])
    @endpoint(native=True)
    def desktop_exchange():
        return jsonify(login.exchange(**body()))

    @app.route("/auth/desktop/session", methods=["GET", "DELETE"])
    @endpoint(native=True)
    def desktop_session():
        header = request.headers.get("Authorization", "")
        token = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
        result = login.device_session(token, revoke=request.method == "DELETE")
        return ("", 204) if request.method == "DELETE" else jsonify(result)
