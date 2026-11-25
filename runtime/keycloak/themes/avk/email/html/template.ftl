<#macro emailLayout>
<!DOCTYPE html>
<html lang="${locale.language}" dir="${(ltr)?then('ltr','rtl')}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f6f5;font-family:system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif;color:#1b2b29;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f6f5;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:8px;border-top:4px solid #0f766e;">
        <tr><td style="padding:20px 28px 0 28px;font-size:20px;font-weight:700;">
          <span style="background:#0f766e;color:#ffffff;padding:2px 8px;border-radius:5px;">AVK</span> Explorer
        </td></tr>
        <tr><td style="padding:12px 28px 8px 28px;font-size:16px;line-height:1.5;">
          <#nested>
        </td></tr>
        <tr><td style="padding:8px 28px 24px 28px;font-size:13px;color:#5b6b69;">
          Questions? Reply to this email or write to support@avkaiexplorer.com.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
</#macro>
