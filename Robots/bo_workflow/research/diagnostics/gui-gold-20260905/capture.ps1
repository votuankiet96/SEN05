$ErrorActionPreference='Stop'
try {
    Add-Type -AssemblyName System.Drawing
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $p=Get-Process -Name cTrader | Select-Object -First 1
    $condition=New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::ProcessIdProperty),$p.Id
    $windows=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,$condition)
    $windowList=foreach($w in $windows){[pscustomobject]@{Name=$w.Current.Name;Bounds=$w.Current.BoundingRectangle.ToString();Handle=$w.Current.NativeWindowHandle}}
    $windowList | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'windows.json')
    foreach($w in $windows){
        $rect=$w.Current.BoundingRectangle
        if(-not $rect.IsEmpty -and $rect.Width -gt 500 -and $rect.Height -gt 300){
            $bitmap=New-Object System.Drawing.Bitmap ([int]$rect.Width),([int]$rect.Height)
            $graphics=[System.Drawing.Graphics]::FromImage($bitmap)
            $graphics.CopyFromScreen([int]$rect.X,[int]$rect.Y,0,0,$bitmap.Size)
            $bitmap.Save((Join-Path $PSScriptRoot 'ctrader.png'),[System.Drawing.Imaging.ImageFormat]::Png)
            $graphics.Dispose(); $bitmap.Dispose()
            break
        }
    }
} catch { $_.ToString() | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'error.txt') }
