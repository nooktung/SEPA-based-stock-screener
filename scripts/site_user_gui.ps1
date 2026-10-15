# A small window to set the password of an account of the web copy's login (python -m src.main
# site-user add), for when the console's hidden prompt does not take keys. The password goes to
# Python on standard input -- never on a command line, in a file or a log -- and only its hash is
# kept (src/web/site_users.py). Takes effect from the next deploy (scripts\deploy_site.ps1).
#   powershell -ExecutionPolicy Bypass -File scripts\site_user_gui.ps1 [-Name admin] [-Role owner|viewer]
param([string]$Name = "admin", [ValidateSet("owner", "viewer")][string]$Role = "owner")

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    $python = if ($found) { $found.Source } else { "python" }
}
$title = "Tài khoản web SEPA"

$form = New-Object System.Windows.Forms.Form
$form.Text = $title
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.TopMost = $true
$form.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$form.ClientSize = New-Object System.Drawing.Size(400, 330)

function Add-Label([string]$text, [int]$y) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $text
    $l.Location = New-Object System.Drawing.Point(20, $y)
    $l.AutoSize = $true
    $form.Controls.Add($l)
}
function Add-Box([int]$y, [bool]$secret) {
    $b = New-Object System.Windows.Forms.TextBox
    $b.Location = New-Object System.Drawing.Point(20, $y)
    $b.Width = 360
    $b.UseSystemPasswordChar = $secret
    $form.Controls.Add($b)
    return $b
}

Add-Label "Tài khoản" 14
$nameBox = Add-Box 36 $false
$nameBox.Text = $Name
$roleText = if ($Role -eq "owner") { "chủ (tài khoản của bạn)" } else { "người xem (có danh sách riêng, tối đa 40 mã)" }
Add-Label "Vai trò: $roleText" 68
Add-Label "Mật khẩu (ít nhất 8 ký tự; nên tắt bộ gõ tiếng Việt)" 100
$pw1 = Add-Box 122 $true
Add-Label "Nhập lại mật khẩu" 156
$pw2 = Add-Box 178 $true
$show = New-Object System.Windows.Forms.CheckBox
$show.Text = "Hiện mật khẩu"
$show.Location = New-Object System.Drawing.Point(20, 212)
$show.AutoSize = $true
$show.Add_CheckedChanged({ $pw1.UseSystemPasswordChar = -not $show.Checked; $pw2.UseSystemPasswordChar = -not $show.Checked })
$form.Controls.Add($show)
$hint = New-Object System.Windows.Forms.Label
$hint.Text = "Mỗi người xem nên có tài khoản riêng. Đổi mật khẩu: chạy lại với cùng tên."
$hint.ForeColor = [System.Drawing.Color]::DimGray
$hint.Location = New-Object System.Drawing.Point(20, 240)
$hint.Size = New-Object System.Drawing.Size(360, 36)
$form.Controls.Add($hint)

$ok = New-Object System.Windows.Forms.Button
$ok.Text = "Lưu"
$ok.Location = New-Object System.Drawing.Point(204, 284)
$ok.Width = 85
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = "Hủy"
$cancel.Location = New-Object System.Drawing.Point(295, 284)
$cancel.Width = 85
$cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.AcceptButton = $ok
$form.CancelButton = $cancel
$form.Controls.Add($ok)
$form.Controls.Add($cancel)
$ok.Add_Click({
    if ($pw1.Text -ne $pw2.Text) {
        [System.Windows.Forms.MessageBox]::Show("Hai lần nhập không khớp.", $title) | Out-Null
        return
    }
    if ($pw1.Text.Length -lt 8) {
        [System.Windows.Forms.MessageBox]::Show("Mật khẩu cần ít nhất 8 ký tự.", $title) | Out-Null
        return
    }
    $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $form.Close()
})
$form.Add_Shown({ $form.Activate(); $pw1.Select() })
if ($form.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit 1 }

# UTF-8 both ways, so a password with any character reaches Python exactly as typed.
$env:PYTHONIOENCODING = "utf-8"
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
$out = $pw1.Text | & $python -m src.main site-user add $nameBox.Text.Trim() --role $Role --password-stdin 2>&1 |
    ForEach-Object { "$_" } | Out-String
$code = $LASTEXITCODE
$pw1.Text = ""
$pw2.Text = ""
$icon = if ($code -eq 0) { [System.Windows.Forms.MessageBoxIcon]::Information } else { [System.Windows.Forms.MessageBoxIcon]::Warning }
[System.Windows.Forms.MessageBox]::Show($out.Trim(), $title, [System.Windows.Forms.MessageBoxButtons]::OK, $icon) | Out-Null
exit $code
