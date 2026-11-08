Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$mdPath = "c:\Users\teoac\Desktop\DFSha\docs\INFORME_TECNICO_FINAL_DFSha.md"
$templateDocx = "c:\Users\teoac\Desktop\DFSha\docs\DFSha_Fase2_Matriz_y_Decisiones_v2.docx"
$outDocx = "c:\Users\teoac\Desktop\DFSha\docs\INFORME_TECNICO_FINAL_DFSha.docx"

$lines = Get-Content $mdPath -Encoding UTF8

function Escape-Xml([string]$s) {
    if ([string]::IsNullOrEmpty($s)) { return "" }
    return $s.Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;').Replace('"', '&quot;')
}

function Format-Runs([string]$text) {
    # Parses bold **text** and inline `code`
    $sb = New-Object System.Text.StringBuilder
    $pattern = '(\*\*([^*]+)\*\*|`([^`]+)`|([^*`]+))'
    $matches2 = [regex]::Matches($text, $pattern)
    foreach ($m in $matches2) {
        $val = $m.Value
        if ($val.StartsWith('**') -and $val.EndsWith('**') -and $val.Length -gt 4) {
            $inner = Escape-Xml $val.Substring(2, $val.Length - 4)
            [void]$sb.Append("<w:r><w:rPr><w:b/></w:rPr><w:t xml:space=""preserve"">$inner</w:t></w:r>")
        } elseif ($val.StartsWith('`') -and $val.EndsWith('`') -and $val.Length -gt 2) {
            $inner = Escape-Xml $val.Substring(1, $val.Length - 2)
            [void]$sb.Append("<w:r><w:rPr><w:rFonts w:ascii=""Consolas"" w:hAnsi=""Consolas""/><w:color w:val=""990000""/></w:rPr><w:t xml:space=""preserve"">$inner</w:t></w:r>")
        } else {
            $escaped = Escape-Xml $val
            [void]$sb.Append("<w:r><w:t xml:space=""preserve"">$escaped</w:t></w:r>")
        }
    }
    return $sb.ToString()
}

$xmlBody = New-Object System.Text.StringBuilder

$inCodeBlock = $false
$inTable = $false
$tableRows = New-Object System.Collections.Generic.List[string]

function Flush-Table {
    if ($tableRows.Count -eq 0) { return }
    [void]$xmlBody.Append("<w:tbl>")
    [void]$xmlBody.Append("<w:tblPr><w:tblW w:w=""5000"" w:type=""pct""/><w:tblBorders><w:top w:val=""single"" w:sz=""4"" w:space=""0"" w:color=""CCCCCC""/><w:left w:val=""none""/><w:bottom w:val=""single"" w:sz=""8"" w:space=""0"" w:color=""666666""/><w:right w:val=""none""/><w:insideH w:val=""single"" w:sz=""4"" w:space=""0"" w:color=""EEEEEE""/><w:insideV w:val=""none""/></w:tblBorders></w:tblPr>")

    $isFirst = $true
    foreach ($r in $tableRows) {
        # Check if separator row like |:---|:---|
        if ($r -match '^[\|\s\-:]+$') { continue }
        
        $cells = $r.Trim().Trim('|').Split('|')
        [void]$xmlBody.Append("<w:tr>")
        if ($isFirst) {
            [void]$xmlBody.Append("<w:trPr><w:tblHeader/></w:trPr>")
        }
        foreach ($c in $cells) {
            $cellText = $c.Trim()
            [void]$xmlBody.Append("<w:tc><w:tcPr><w:tcMar><w:top w:w=""120"" w:type=""dxa""/><w:bottom w:w=""120"" w:type=""dxa""/><w:left w:w=""140"" w:type=""dxa""/><w:right w:w=""140"" w:type=""dxa""/></w:tcMar>")
            if ($isFirst) {
                [void]$xmlBody.Append("<w:shd w:val=""clear"" w:color=""auto"" w:fill=""F2F4F7""/>")
            }
            [void]$xmlBody.Append("</w:tcPr>")
            if ($isFirst) {
                [void]$xmlBody.Append("<w:p><w:pPr><w:pStyle w:val=""Normal""/><w:spacing w:after=""60"" w:before=""60""/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t xml:space=""preserve"">$(Escape-Xml $cellText)</w:t></w:r></w:p>")
            } else {
                $runs = Format-Runs $cellText
                [void]$xmlBody.Append("<w:p><w:pPr><w:pStyle w:val=""Normal""/><w:spacing w:after=""40"" w:before=""40""/></w:pPr>$runs</w:p>")
            }
            [void]$xmlBody.Append("</w:tc>")
        }
        [void]$xmlBody.Append("</w:tr>")
        $isFirst = $false
    }
    [void]$xmlBody.Append("</w:tbl>")
    $tableRows.Clear()
}

foreach ($line in $lines) {
    $t = $line.TrimEnd()

    # Code block toggle
    if ($t.StartsWith('```')) {
        if ($inTable) { Flush-Table; $inTable = $false }
        $inCodeBlock = -not $inCodeBlock
        continue
    }

    if ($inCodeBlock) {
        $escaped = Escape-Xml $t
        [void]$xmlBody.Append("<w:p><w:pPr><w:pStyle w:val=""Normal""/><w:spacing w:line=""240"" w:lineRule=""auto"" w:after=""40"" w:before=""0""/><w:shd w:val=""clear"" w:color=""auto"" w:fill=""F8F9FA""/></w:pPr><w:r><w:rPr><w:rFonts w:ascii=""Consolas"" w:hAnsi=""Consolas""/><w:sz w:val=""18""/><w:color w:val=""24292E""/></w:rPr><w:t xml:space=""preserve"">$escaped</w:t></w:r></w:p>")
        continue
    }

    # Markdown Table detection
    if ($t.StartsWith('|') -and $t.EndsWith('|')) {
        $inTable = $true
        $tableRows.Add($t)
        continue
    } else {
        if ($inTable) {
            Flush-Table
            $inTable = $false
        }
    }

    # Headings
    if ($t.StartsWith('# ')) {
        $title = $t.Substring(2).Trim()
        [void]$xmlBody.Append("<w:p><w:pPr><w:pStyle w:val=""Title""/><w:spacing w:before=""360"" w:after=""160""/></w:pPr><w:r><w:rPr><w:b/><w:color w:val=""1A365D""/></w:rPr><w:t>$(Escape-Xml $title)</w:t></w:r></w:p>")
        continue
    }
    if ($t.StartsWith('## ')) {
        $h1 = $t.Substring(3).Trim()
        [void]$xmlBody.Append("<w:p><w:pPr><w:pStyle w:val=""Heading1""/><w:spacing w:before=""320"" w:after=""140""/></w:pPr><w:r><w:rPr><w:b/><w:color w:val=""2B6CB0""/></w:rPr><w:t>$(Escape-Xml $h1)</w:t></w:r></w:p>")
        continue
    }
    if ($t.StartsWith('### ')) {
        $h2 = $t.Substring(4).Trim()
        [void]$xmlBody.Append("<w:p><w:pPr><w:pStyle w:val=""Heading2""/><w:spacing w:before=""240"" w:after=""100""/></w:pPr><w:r><w:rPr><w:b/><w:color w:val=""2D3748""/></w:rPr><w:t>$(Escape-Xml $h2)</w:t></w:r></w:p>")
        continue
    }
    if ($t.StartsWith('#### ')) {
        $h3 = $t.Substring(5).Trim()
        [void]$xmlBody.Append("<w:p><w:pPr><w:pStyle w:val=""Heading3""/><w:spacing w:before=""180"" w:after=""80""/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>$(Escape-Xml $h3)</w:t></w:r></w:p>")
        continue
    }

    # Horizontal Rule
    if ($t -eq '---' -or $t -eq '***') {
        [void]$xmlBody.Append("<w:p><w:pPr><w:pBdr><w:bottom w:val=""single"" w:sz=""6"" w:space=""1"" w:color=""CBD5E0""/></w:pBdr><w:spacing w:before=""120"" w:after=""120""/></w:pPr></w:p>")
        continue
    }

    # Bullet lists
    if ($t -match '^\s*[-*]\s+(.+)$') {
        $content = $matches[1]
        $runs = Format-Runs $content
        [void]$xmlBody.Append("<w:p><w:pPr><w:pStyle w:val=""ListParagraph""/><w:spacing w:after=""60""/></w:pPr><w:r><w:rPr><w:rFonts w:ascii=""Symbol"" w:hAnsi=""Symbol""/></w:rPr><w:t>&#xb7; </w:t></w:r>$runs</w:p>")
        continue
    }

    # Numbered list
    if ($t -match '^\s*(\d+)\.\s+(.+)$') {
        $num = $matches[1]
        $content = $matches[2]
        $runs = Format-Runs $content
        [void]$xmlBody.Append("<w:p><w:pPr><w:pStyle w:val=""ListParagraph""/><w:spacing w:after=""60""/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>$num. </w:t></w:r>$runs</w:p>")
        continue
    }

    # Empty line
    if ([string]::IsNullOrWhiteSpace($t)) {
        continue
    }

    # Normal paragraph
    $runs = Format-Runs $t
    [void]$xmlBody.Append("<w:p><w:pPr><w:pStyle w:val=""Normal""/><w:spacing w:after=""120"" w:line=""276"" w:lineRule=""auto""/></w:pPr>$runs</w:p>")
}

if ($inTable) { Flush-Table }

# Construct complete document.xml
$fullDocXml = @"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<w:body>
$($xmlBody.ToString())
<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>
</w:body>
</w:document>
"@

# Copy template docx to destination
Copy-Item -Path $templateDocx -Destination $outDocx -Force

# Open and replace word/document.xml
$zip = [System.IO.Compression.ZipFile]::Open($outDocx, [System.IO.Compression.ZipArchiveMode]::Update)
$docEntry = $zip.GetEntry('word/document.xml')
if ($docEntry) {
    $docEntry.Delete()
}
$newEntry = $zip.CreateEntry('word/document.xml', [System.IO.Compression.CompressionLevel]::Optimal)
$writer = New-Object System.IO.StreamWriter($newEntry.Open(), [System.Text.Encoding]::UTF8)
$writer.Write($fullDocXml)
$writer.Flush()
$writer.Close()
$zip.Dispose()

Write-Host "SUCCESS: Generated $outDocx"
$item = Get-Item $outDocx
Write-Host "File size: $($item.Length) bytes"
