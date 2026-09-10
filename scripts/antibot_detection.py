"""Passive DOM/resource inspection inspired by Antibot-Detector's approach.

Independent implementation, not a copy of Scrapfly's extension or rule database.
Provider presence is diagnostic; only a visible challenge blocks collection.
No cookies, tokens, form contents, or resource URLs leave the browser.
"""

from typing import Any

DETECTION_SCRIPT = r"""
(() => {
  const visible = el => {
    if (!el || !el.getClientRects().length) return false;
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden' ||
          style.visibility === 'collapse' || Number(style.opacity) === 0) return false;
    }
    return true;
  };
  const rules = [
    ['recaptcha', 'reCAPTCHA', /(?:google\.com|recaptcha\.net)\/recaptcha\//i,
     'iframe[src*="/recaptcha/api2/bframe"],iframe[src*="/recaptcha/enterprise/bframe"]'],
    ['hcaptcha', 'hCaptcha', /(?:^|\.)hcaptcha\.com\//i,
     'iframe[src*="hcaptcha.com"][src*="frame=challenge"]'],
    ['turnstile', 'Cloudflare Turnstile', /challenges\.cloudflare\.com\/turnstile\//i,
     'iframe[src*="challenges.cloudflare.com"]'],
    ['geetest', 'GeeTest', /(?:^|\.)geetest\.com\//i,
     '.geetest_panel,.geetest_holder,.geetest_box'],
    ['netease', '网易易盾', /(?:^|\.)(?:dun\.163\.com|nosdn\.127\.net)\//i,
     '.yidun_popup,.yidun_panel,.yidun_control'],
    ['tencent', '腾讯验证码', /(?:^|\.)(?:captcha\.qq\.com|turing\.captcha\.qcloud\.com)\//i,
     '#tcaptcha_transform_dy,iframe[src*="captcha.qq.com"]'],
    ['aliyun', '阿里云验证码', /(?:^|\.)(?:captcha\.aliyuncs\.com|g\.alicdn\.com\/sd\/nc)\//i,
     '.nc-container,.nc_scale,#aliyunCaptcha-window-popup'],
    ['datadome', 'DataDome', /(?:^|\.)(?:captcha-delivery\.com|datadome\.co)\//i,
     'iframe[src*="captcha-delivery.com"]'],
    ['cloudflare', 'Cloudflare', /\/cdn-cgi\/challenge-platform\//i,
     '#challenge-running,#challenge-stage']
  ];
  const resources = [...document.querySelectorAll('script[src],iframe[src]')].map(e => e.src);
  if (typeof performance.getEntriesByType === 'function') {
    resources.push(...performance.getEntriesByType('resource').map(e => e.name));
  }
  const locations = resources.slice(0, 2000).flatMap(raw => {
    try { const u = new URL(raw, location.href); return [u.hostname + u.pathname]; }
    catch { return []; }
  });
  const detections = [];
  for (const [id, name, pattern, selector] of rules) {
    const active = [...document.querySelectorAll(selector)].some(visible);
    if (active || locations.some(url => pattern.test(url))) {
      detections.push({id, name, evidence: active ? 'visible_widget' : 'resource',
                       requires_human: active});
    }
  }
  return {version: '1', detections};
})()
"""

PROVIDERS = {
    "recaptcha": "reCAPTCHA",
    "hcaptcha": "hCaptcha",
    "turnstile": "Cloudflare Turnstile",
    "geetest": "GeeTest",
    "netease": "网易易盾",
    "tencent": "腾讯验证码",
    "aliyun": "阿里云验证码",
    "datadome": "DataDome",
    "cloudflare": "Cloudflare",
}


def summarize_detection(page: dict[str, Any]) -> dict[str, Any]:
    """Persist only allowlisted diagnostic fields, never arbitrary page data."""
    raw = page.get("antibot", {})
    candidates = raw.get("detections", []) if isinstance(raw, dict) else []
    detections = []
    seen = set()
    for row in candidates if isinstance(candidates, list) else []:
        if not isinstance(row, dict):
            continue
        provider = row.get("id")
        if not isinstance(provider, str) or provider not in PROVIDERS or provider in seen:
            continue
        seen.add(provider)
        evidence = row.get("evidence")
        active = evidence == "visible_widget" and row.get("requires_human") is True
        detections.append(
            {
                "id": provider,
                "name": PROVIDERS[provider],
                "requires_human": active,
                "evidence": "visible_widget" if active else "resource",
            }
        )
    return {
        "version": "1",
        "detections": detections,
        "requires_human": any(row["requires_human"] for row in detections),
    }
