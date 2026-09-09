$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $items = @()
    $roots = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($root in $roots) {
        $items += [pscustomobject]@{ Depth=0; Name=$root.Current.Name; Type=$root.Current.ControlType.ProgrammaticName; Id=$root.Current.AutomationId; ProcessId=$root.Current.ProcessId }
        if ($root.Current.Name -match 'cTrader|FTMO') {
            $children = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
            foreach ($child in $children) {
                $items += [pscustomobject]@{ Depth=1; Name=$child.Current.Name; Type=$child.Current.ControlType.ProgrammaticName; Id=$child.Current.AutomationId; ProcessId=$child.Current.ProcessId; Bounds=$child.Current.BoundingRectangle.ToString(); Help=$child.Current.HelpText; Offscreen=$child.Current.IsOffscreen }
            }
        }
    }
    $items | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'desktop-elements.json') -Encoding UTF8
    Add-Type -AssemblyName System.Drawing
    Add-Type -AssemblyName System.Windows.Forms
    $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bitmap = New-Object System.Drawing.Bitmap $bounds.Width,$bounds.Height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($bounds.X,$bounds.Y,0,0,$bitmap.Size)
    $bitmap.Save((Join-Path $PSScriptRoot 'desktop.png'))
    $graphics.Dispose()
    $bitmap.Dispose()
} catch { $_.ToString() | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'desktop-error.txt') -Encoding UTF8 }
