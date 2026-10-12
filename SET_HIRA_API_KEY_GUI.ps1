$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = 'HIRA API Key Setup'
$form.StartPosition = 'CenterScreen'
$form.ClientSize = New-Object System.Drawing.Size(560, 190)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.TopMost = $true

$guide = New-Object System.Windows.Forms.Label
$guide.Location = New-Object System.Drawing.Point(20, 18)
$guide.Size = New-Object System.Drawing.Size(520, 45)
$guide.Text = "Paste the Data.go.kr service key (Encoding or Decoding).`r`nThe key is not stored in project files or the insurance database."
$form.Controls.Add($guide)

$keyTextBox = New-Object System.Windows.Forms.TextBox
$keyTextBox.Location = New-Object System.Drawing.Point(20, 76)
$keyTextBox.Size = New-Object System.Drawing.Size(520, 25)
$keyTextBox.UseSystemPasswordChar = $true
$form.Controls.Add($keyTextBox)

$save = New-Object System.Windows.Forms.Button
$save.Location = New-Object System.Drawing.Point(350, 125)
$save.Size = New-Object System.Drawing.Size(90, 34)
$save.Text = 'Save'
$save.Add_Click({
    $key = $keyTextBox.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($key)) {
        [System.Windows.Forms.MessageBox]::Show('Enter the API key.', 'Missing key') | Out-Null
        return
    }
    try {
        [Environment]::SetEnvironmentVariable('HIRA_DISEASE_API_KEY', $key, 'User')
        $savedKey = [Environment]::GetEnvironmentVariable('HIRA_DISEASE_API_KEY', 'User')
        if (-not [string]::Equals($savedKey, $key, [System.StringComparison]::Ordinal)) {
            throw 'Windows did not retain the API key. Try running this file as administrator.'
        }
        [System.Windows.Forms.MessageBox]::Show(
            'Setup completed. Close and restart the insurance app.',
            'Setup completed'
        ) | Out-Null
        $keyTextBox.Clear()
        $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $form.Close()
    } catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Setup error') | Out-Null
    } finally {
        $savedKey = $null
        $key = $null
    }
})
$form.Controls.Add($save)

$cancel = New-Object System.Windows.Forms.Button
$cancel.Location = New-Object System.Drawing.Point(450, 125)
$cancel.Size = New-Object System.Drawing.Size(90, 34)
$cancel.Text = 'Cancel'
$cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($cancel)

$form.AcceptButton = $save
$form.CancelButton = $cancel
$form.Add_Shown({ $keyTextBox.Focus() })
[void]$form.ShowDialog()
