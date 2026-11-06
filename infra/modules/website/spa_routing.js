// CloudFront Function (viewer request): single-page-app routing.
// "/" and paths without a file extension ("/labs/3am") get the app shell /index.html;
// files ("/assets/app.3f2a.js", "/favicon.svg") pass through unchanged.
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri.endsWith("/") || uri.lastIndexOf(".") < uri.lastIndexOf("/")) {
    request.uri = "/index.html";
  }
  return request;
}
