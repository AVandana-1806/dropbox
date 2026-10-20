<html>
<body style="margin:0;padding:24px;background-color:#f4f4f4;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td align="center">

<table width="700" cellpadding="0" cellspacing="0" border="0" style="background-color:#ffffff;border:1px solid #d0d0d0;">

  <tr>
    <td style="background-color:#a32d2d;padding:16px 22px;">
      <div style="color:#ffffff;font-size:18px;font-weight:bold;">Pipeline Alert: {alarm_name}</div>
      <div style="color:#f2d6d6;font-size:13px;padding-top:4px;">{environment} &middot; {region} &middot; {timestamp}</div>
    </td>
  </tr>

  <tr>
    <td style="padding:18px 22px 8px 22px;">
      <div style="font-size:14px;color:#222222;line-height:1.5;">{summary}</div>
    </td>
  </tr>

  <tr>
    <td style="padding:8px 22px 4px 22px;">
      <div style="font-size:12px;font-weight:bold;color:#666666;">ALARM DETAIL</div>
    </td>
  </tr>
  <tr>
    <td style="padding:4px 22px 0 22px;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-size:13px;">
        <tr style="background-color:#f0f0f0;">
          <td style="padding:8px 10px;border:1px solid #d8d8d8;color:#555555;width:28%;">Field</td>
          <td style="padding:8px 10px;border:1px solid #d8d8d8;color:#555555;">Value</td>
        </tr>
        <tr>
          <td style="padding:8px 10px;border:1px solid #d8d8d8;">Metric</td>
          <td style="padding:8px 10px;border:1px solid #d8d8d8;">{metric_name} ({namespace})</td>
        </tr>
        <tr style="background-color:#fafafa;">
          <td style="padding:8px 10px;border:1px solid #d8d8d8;">Condition</td>
          <td style="padding:8px 10px;border:1px solid #d8d8d8;">{condition}</td>
        </tr>
        <tr>
          <td style="padding:8px 10px;border:1px solid #d8d8d8;">Reason</td>
          <td style="padding:8px 10px;border:1px solid #d8d8d8;">{reason}</td>
        </tr>
      </table>
    </td>
  </tr>

  <tr>
    <td style="padding:18px 22px 4px 22px;">
      <div style="font-size:12px;font-weight:bold;color:#666666;">PIPELINE STATE</div>
    </td>
  </tr>
  <tr>
    <td style="padding:4px 22px 0 22px;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-size:13px;">
        <tr style="background-color:#f0f0f0;">
          <td style="padding:8px 10px;border:1px solid #d8d8d8;color:#555555;width:34%;">Stage</td>
          <td style="padding:8px 10px;border:1px solid #d8d8d8;color:#555555;width:38%;">Signal</td>
          <td style="padding:8px 10px;border:1px solid #d8d8d8;color:#555555;">Value</td>
        </tr>
        {state_rows}
      </table>
    </td>
  </tr>

  <tr>
    <td style="padding:20px 22px 24px 22px;">
      <a href="{alarm_url}" style="display:inline-block;background-color:#1f3a5f;color:#ffffff;text-decoration:none;font-size:13px;padding:9px 14px;margin-right:8px;">View Alarm</a>
      <a href="{logs_url}" style="display:inline-block;background-color:#ffffff;color:#1f3a5f;text-decoration:none;font-size:13px;padding:9px 14px;border:1px solid #1f3a5f;">Lambda Logs</a>
    </td>
  </tr>

  <tr>
    <td style="background-color:#f7f7f7;border-top:1px solid #e0e0e0;padding:12px 22px;">
      <div style="font-size:11px;color:#888888;">CloudWatch-to-Splunk log pipeline &middot; automated alert</div>
    </td>
  </tr>

</table>

</td></tr>
</table>
</body>
</html>
