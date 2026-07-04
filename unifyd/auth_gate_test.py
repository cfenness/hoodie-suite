"""Plain-python tests for auth_gate claim verification: python auth_gate_test.py"""
import base64, json, time
import auth_gate

CID = "123.apps.googleusercontent.com"
ALLOW = {"chris.fennessey1@gmail.com"}


def claims(**over):
    c = {"iss": "https://accounts.google.com", "aud": CID, "nonce": "N",
         "exp": int(time.time()) + 300, "email_verified": True,
         "email": "chris.fennessey1@gmail.com"}
    c.update(over)
    return c


def expect_ok(name, c):
    try:
        email = auth_gate.verify_claims(c, CID, ALLOW, "N")
        assert email == "chris.fennessey1@gmail.com", email
        print("ok  ", name)
        return 1
    except Exception as e:
        print("FAIL", name, "->", e)
        return 0


def expect_reject(name, c, nonce="N"):
    try:
        auth_gate.verify_claims(c, CID, ALLOW, nonce)
        print("FAIL", name, "-> accepted but should reject")
        return 0
    except ValueError:
        print("ok  ", name, "(rejected)")
        return 1


def test_jwt_decode():
    payload = {"email": "x@y.com", "aud": CID}
    seg = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    tok = "hdr.%s.sig" % seg
    got = auth_gate._decode_jwt_payload(tok)
    assert got == payload, got
    print("ok   jwt-decode")
    return 1


passed = 0
passed += expect_ok("valid", claims())
passed += expect_reject("bad-issuer", claims(iss="evil.com"))
passed += expect_reject("aud-mismatch", claims(aud="other"))
passed += expect_reject("nonce-mismatch", claims(nonce="X"))
passed += expect_reject("expired", claims(exp=int(time.time()) - 3600))
passed += expect_reject("unverified", claims(email_verified=False))
passed += expect_reject("not-allowlisted", claims(email="stranger@gmail.com"))
passed += expect_reject("no-email", claims(email=""))
passed += test_jwt_decode()

# ---- mobile bearer tokens ----
def ok(name, cond):
    global passed
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else " (expected pass)"))
    passed += 1 if cond else 0

import os
os.environ["SESSION_SECRET"] = "test-session-secret-0123456789abcdef"
os.environ["ALLOWED_EMAILS"] = "chris.fennessey1@gmail.com"

_tok = auth_gate.mint_mobile_token("chris.fennessey1@gmail.com")
ok("mint/verify round-trip", auth_gate.verify_mobile_token(_tok) == "chris.fennessey1@gmail.com")
ok("tampered token rejected", auth_gate.verify_mobile_token(_tok + "x") is None)
ok("non-allowlisted token rejected",
   auth_gate.verify_mobile_token(auth_gate.mint_mobile_token("stranger@gmail.com")) is None)

# gate accepts a valid Bearer on /api/*
from flask import Flask, jsonify as _fjson
os.environ["GOOGLE_CLIENT_ID"] = "cid"
os.environ["GOOGLE_CLIENT_SECRET"] = "sec"
_app = Flask(__name__)
auth_gate.init(_app)
@_app.get("/api/x")
def _x():
    return _fjson(ok=True)
_c = _app.test_client()
ok("gate 401 without token", _c.get("/api/x").status_code == 401)
ok("gate 200 with valid bearer",
   _c.get("/api/x", headers={"Authorization": "Bearer " + _tok}).status_code == 200)

TOTAL = 14
print("\n%d/%d passed" % (passed, TOTAL))
raise SystemExit(0 if passed == TOTAL else 1)
