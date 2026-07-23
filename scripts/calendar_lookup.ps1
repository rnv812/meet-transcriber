# Find the calendar meeting matching a recording, via local Outlook COM.
#
# Usage:
#   powershell -NoProfile -File scripts/calendar_lookup.ps1 <recordings folder | YYYY-MM-DD_HH-MM> [-Day]
#
# Default: events overlapping the recording start (meeting may start up to
# 15 min after the recording began). -Day: all events of that date instead —
# fallback for rescheduled meetings.
# Output: JSON array (UTF-8, non-ASCII escaped). Outlook unavailable -> [] and
# exit 0, so callers just treat it as "no calendar info".
param(
    [Parameter(Mandatory = $true)][string]$Target,
    [switch]$Day
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$name = Split-Path -Leaf $Target
if ($name -match '(\d{4})-(\d{2})-(\d{2})[_ T](\d{2})[-:](\d{2})') {
    $recStart = Get-Date -Year $Matches[1] -Month $Matches[2] -Day $Matches[3] `
        -Hour $Matches[4] -Minute $Matches[5] -Second 0
} else {
    [Console]::Error.WriteLine("Cannot parse date/time from: $Target (expected YYYY-MM-DD_HH-MM)")
    exit 1
}

if ($Day) {
    $winStart = $recStart.Date
    $winEnd   = $recStart.Date.AddDays(1)
} else {
    $winStart = $recStart
    $winEnd   = $recStart.AddMinutes(15)
}

try {
    $ol  = New-Object -ComObject Outlook.Application
    $ns  = $ol.GetNamespace('MAPI')
    $cal = $ns.GetDefaultFolder(9)  # olFolderCalendar
    $items = $cal.Items
    $items.Sort('[Start]')
    $items.IncludeRecurrences = $true
    # Overlap filter; both bounds present, or iterating recurrences never ends.
    # Restrict parses date literals in the current culture -> ToString('g').
    $filter = "[Start] <= '" + $winEnd.ToString('g') + "' AND [End] >= '" + $winStart.ToString('g') + "'"
    $found = $items.Restrict($filter)

    $result = @()
    foreach ($i in $found) {
        if (-not $Day -and $i.AllDayEvent) { continue }  # vacations etc. overlap any time
        $result += [pscustomobject]@{
            subject   = $i.Subject
            start     = $i.Start.ToString('yyyy-MM-dd HH:mm')
            end       = $i.End.ToString('yyyy-MM-dd HH:mm')
            allDay    = [bool]$i.AllDayEvent
            organizer = $i.Organizer
            required  = @(($i.RequiredAttendees -split ';') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
            optional  = @(($i.OptionalAttendees -split ';') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
            location  = $i.Location
        }
    }
    ConvertTo-Json -InputObject $result -Depth 4
} catch {
    [Console]::Error.WriteLine("Outlook lookup failed: $($_.Exception.Message)")
    ConvertTo-Json -InputObject @()
    exit 0
}
