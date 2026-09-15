// Host-side autosize for the WPR gas-price widgets (full + compact). Loaded
// from THIS origin by the embed snippets (<script async src=".../embed.js">)
// instead of pasted inline: WPR's WordPress refuses to save post content
// containing inline <script> code — the security layer blocks the save request
// and the editor shows "Updating failed. The response is not a valid JSON
// response." One include handles every gas widget on the page; extra includes
// no-op.
//
// Only messages from the origin this file was served from are honored (derived
// from the script's own src, so localhost previews work too), and the sender
// must be one of the page's own iframes — so several widgets coexist and
// nothing else on the page can spoof a resize.
(function () {
  if (window.wprGasEmbed) return;
  window.wprGasEmbed = 1;
  var origin = new URL(document.currentScript.src).origin;
  window.addEventListener('message', function (e) {
    if (e.origin !== origin) return;
    if (!e.data || e.data.type !== 'wpr-gas-height') return;
    // A widget is never legitimately 0px; keep the placeholder height instead.
    if (!(e.data.height > 0)) return;
    var frames = document.getElementsByTagName('iframe');
    for (var i = 0; i < frames.length; i++) {
      if (frames[i].contentWindow === e.source) {
        frames[i].style.height = e.data.height + 'px';
        return;
      }
    }
  });
  // The widgets post on every render, but a fast iframe can finish before this
  // (async) script attaches — ping so already-loaded widgets re-post.
  // targetOrigin scopes the ping to gas-widget frames only.
  var ping = function () {
    var frames = document.getElementsByTagName('iframe');
    for (var i = 0; i < frames.length; i++) {
      try { frames[i].contentWindow.postMessage('wpr-gas-height?', origin); } catch (err) {}
    }
  };
  ping();
  window.addEventListener('load', ping);
})();
