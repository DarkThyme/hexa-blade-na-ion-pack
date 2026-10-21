# Open the report in Word, refresh the table of contents and page fields, export the PDF, and save the refreshed .docx.
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File docs\report_builder\word_export.ps1 [-Doc <in.docx>] [-Pdf <out.pdf>]
# Word hung on a plain Save() of the docx-js output on this machine, so the PDF is exported first and the refreshed
# document is written with SaveAs2 (explicit .docx format) to a side file, then moved over the original.
param(
  [string]$Doc = (Join-Path $PSScriptRoot '..\Hexa-Blade_Project_Report.docx'),
  [string]$Pdf = (Join-Path $PSScriptRoot '..\Hexa-Blade_Project_Report.pdf')
)
$Doc = [System.IO.Path]::GetFullPath($Doc)
$Pdf = [System.IO.Path]::GetFullPath($Pdf)
$Out = [System.IO.Path]::ChangeExtension($Doc, '.refreshed.docx')
$w = New-Object -ComObject Word.Application
$w.Visible = $false
$w.DisplayAlerts = 0
try {
  $d = $w.Documents.Open($Doc)
  if ($d.TablesOfContents.Count -gt 0) { $d.TablesOfContents.Item(1).Update() }
  $null = $d.Fields.Update()
  $d.ExportAsFixedFormat($Pdf, 17)
  "pages: " + $d.ComputeStatistics(2)
  $d.SaveAs2($Out, 16)          # 16 = wdFormatXMLDocument (.docx)
  $d.Close(0)
} finally { $w.Quit() }
Move-Item $Out $Doc -Force
