// CloudFront Function (viewer-request) — shared-password gate.
// Attach this to your CloudFront distribution's default behavior so the
// proprietary apps (CRM, MDM) aren't openly browsable. This is a light gate,
// not real auth — upgrade to Cognito/SSO when you have real users.
//
// To generate the token below, base64-encode "username:password", e.g.:
//   echo -n 'hoodie:changeme' | base64   ->  aG9vZGllOmNoYW5nZW1l
function handler(event) {
  var request = event.request;
  var headers = request.headers;

  var expected = "Basic aG9vZGllOmNoYW5nZW1l"; // <-- replace with your own token

  if (!headers.authorization || headers.authorization.value !== expected) {
    return {
      statusCode: 401,
      statusDescription: "Unauthorized",
      headers: {
        "www-authenticate": { value: 'Basic realm="Hoodie Suite"' }
      }
    };
  }
  return request;
}
