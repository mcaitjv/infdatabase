"""
pipeline/notifier.py — Günlük sağlık raporu e-posta bildirimi
-------------------------------------------------------------
Resend API kullanır (resend.com — ücretsiz, SMTP gerektirmez).
RESEND_API_KEY veya ALERT_EMAIL_TO eksikse sessizce atlanır.
"""

import logging
import os

from pipeline.health import PipelineHealthReport

logger = logging.getLogger(__name__)

_STATUS_COLOR = {
    "ok":      "#22c55e",
    "warning": "#f59e0b",
    "error":   "#ef4444",
}
_STATUS_LABEL = {
    "ok":      "✓ TAMAM",
    "warning": "⚠ UYARI",
    "error":   "✗ HATA",
}


def _build_html(report: PipelineHealthReport) -> str:
    status_color = _STATUS_COLOR.get(report.overall_status, "#6b7280")
    status_label = _STATUS_LABEL.get(report.overall_status, report.overall_status.upper())

    rows = ""
    for mod in report.modules:
        col   = _STATUS_COLOR.get(mod.status, "#6b7280")
        badge = _STATUS_LABEL.get(mod.status, mod.status)

        if mod.expected > 0:
            records = f"{mod.records_today} / {mod.expected} beklenen"
        else:
            records = str(mod.records_today)

        if mod.records_yesterday > 0 and mod.records_today > 0:
            pct = (mod.records_today - mod.records_yesterday) / mod.records_yesterday * 100
            sign = "+" if pct >= 0 else ""
            records += f"  <span style='color:#6b7280;font-size:12px'>({sign}{pct:.1f}% dün)</span>"

        missing_html = ""
        if mod.missing:
            items = "".join(f"<li style='margin:2px 0'>{m}</li>" for m in mod.missing[:8])
            more  = f"<li style='color:#6b7280'>... ve {len(mod.missing)-8} eksik daha</li>" if len(mod.missing) > 8 else ""
            missing_html = f"<ul style='margin:6px 0 0 16px;padding:0;font-size:13px'>{items}{more}</ul>"

        anomaly_html = ""
        if mod.anomalies:
            items = "".join(
                f"<li style='margin:2px 0'>{a.identifier[:45]} — "
                f"{a.yesterday:.2f} → <b>{a.today:.2f}</b> TL "
                f"({'+'if a.change_pct>=0 else ''}{a.change_pct:.1f}%)</li>"
                for a in mod.anomalies[:5]
            )
            more = f"<li style='color:#6b7280'>... ve {len(mod.anomalies)-5} anomali daha</li>" if len(mod.anomalies) > 5 else ""
            anomaly_html = (
                f"<div style='font-size:12px;color:#6b7280;margin-top:4px'>Fiyat değişimleri:</div>"
                f"<ul style='margin:4px 0 0 16px;padding:0;font-size:13px'>{items}{more}</ul>"
            )

        rows += f"""
        <tr>
          <td style='padding:12px 16px;border-bottom:1px solid #f3f4f6;vertical-align:top'>
            <div style='font-weight:600'>{mod.module_name}</div>
            <div style='font-size:13px;color:#6b7280;margin-top:2px'>{records}</div>
            {missing_html}{anomaly_html}
          </td>
          <td style='padding:12px 16px;border-bottom:1px solid #f3f4f6;vertical-align:top;white-space:nowrap'>
            <span style='background:{col};color:#fff;padding:3px 10px;border-radius:12px;font-size:13px;font-weight:600'>
              {badge}
            </span>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style='font-family:system-ui,sans-serif;background:#f9fafb;margin:0;padding:24px'>
  <div style='max-width:640px;margin:0 auto;background:#fff;border-radius:12px;
              box-shadow:0 1px 4px rgba(0,0,0,.08);overflow:hidden'>

    <!-- Header -->
    <div style='background:{status_color};padding:20px 24px'>
      <div style='font-size:20px;font-weight:700;color:#fff'>
        infdatabase &nbsp;·&nbsp; Günlük Sağlık Raporu
      </div>
      <div style='font-size:14px;color:rgba(255,255,255,.85);margin-top:4px'>
        {report.date} &nbsp;·&nbsp; {status_label}
      </div>
    </div>

    <!-- Modül tablosu -->
    <table style='width:100%;border-collapse:collapse'>
      <thead>
        <tr style='background:#f9fafb'>
          <th style='padding:10px 16px;text-align:left;font-size:12px;
                     color:#6b7280;font-weight:600;border-bottom:1px solid #e5e7eb'>MODÜL</th>
          <th style='padding:10px 16px;text-align:left;font-size:12px;
                     color:#6b7280;font-weight:600;border-bottom:1px solid #e5e7eb'>DURUM</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>

    <!-- Footer -->
    <div style='padding:16px 24px;background:#f9fafb;font-size:12px;color:#9ca3af'>
      Detaylı rapor: <code>logs/health_{report.date}.json</code>
    </div>
  </div>
</body>
</html>"""


def send_health_email(report: PipelineHealthReport) -> bool:
    """
    Sağlık raporunu HTML e-posta olarak gönderir.
    Döndürür: True → gönderildi, False → config eksik veya hata.
    """
    api_key  = os.environ.get("RESEND_API_KEY", "").strip()
    to_email = os.environ.get("ALERT_EMAIL_TO", "").strip()

    if not api_key or not to_email:
        logger.debug("[notifier] RESEND_API_KEY veya ALERT_EMAIL_TO eksik — mail atlanıyor")
        return False

    try:
        import resend as _resend
        _resend.api_key = api_key

        status_label = _STATUS_LABEL.get(report.overall_status, report.overall_status.upper())
        subject = f"[infdatabase] {report.date} — {status_label}"

        _resend.Emails.send({
            "from":    "infdatabase <onboarding@resend.dev>",
            "to":      [to_email],
            "subject": subject,
            "html":    _build_html(report),
        })

        logger.info("[notifier] Sağlık raporu gönderildi → %s", to_email)
        return True

    except Exception as exc:
        logger.warning("[notifier] Mail gönderilemedi: %s", exc)
        return False
