$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $request = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'ui-request.json') -Raw | ConvertFrom-Json
    $window = [System.Windows.Automation.AutomationElement]::RootElement.FindFirst(
        [System.Windows.Automation.TreeScope]::Children,
        (New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::AutomationIdProperty),'MainWindow_AId'))
    $condition = New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::AutomationIdProperty),([string]$request.Id)
    $elements = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition)
    $element = $elements[[int]$request.Index]
    $patterns = @($element.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName })
    if ($request.Action -eq 'Select') {
        $element.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
    } elseif ($request.Action -eq 'Invoke') {
        $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    } elseif ($request.Action -eq 'Click') {
        Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class DesktopClick {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr h, ref Point p);
    [StructLayout(LayoutKind.Sequential)] public struct Point { public int X; public int Y; }
}
'@
        $rect = $element.Current.BoundingRectangle
        $point = New-Object DesktopClick+Point
        $point.X = [int]($rect.X + $rect.Width / 2)
        $point.Y = [int]($rect.Y + $rect.Height / 2)
        if ($null -ne $request.X) { $point.X = [int]$request.X; $point.Y = [int]$request.Y }
        [DesktopClick]::SetCursorPos($point.X,$point.Y) | Out-Null
        [DesktopClick]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
        [DesktopClick]::mouse_event(4,0,0,0,[UIntPtr]::Zero)
        $handle = [IntPtr]$window.Current.NativeWindowHandle
    }
    [pscustomobject]@{ Request=$request; Name=$element.Current.Name; Patterns=$patterns; Success=$true } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'ui-result.json') -Encoding UTF8
    Start-Sleep -Seconds 2
    & (Join-Path $PSScriptRoot 'Inspect-Desktop.ps1')
} catch {
    [pscustomobject]@{Success=$false; Error=$_.ToString()} | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $PSScriptRoot 'ui-result.json') -Encoding UTF8
}
