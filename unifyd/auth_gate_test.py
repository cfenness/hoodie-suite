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

TOTAL = 9
print("\n%d/%d passed" % (passed, TOTAL))
raise SystemExit(0 if passed == TOTAL else 1)
