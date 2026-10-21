# Generate SVG, PNG and Windows ICO from the same vector paths. No runtime dependency.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$projectRoot = Split-Path -Parent $PSScriptRoot
$brandDirectory = Join-Path $projectRoot 'static/brand'
[void][System.IO.Directory]::CreateDirectory($brandDirectory)
$utf8 = New-Object System.Text.UTF8Encoding($false)
$invariant = [System.Globalization.CultureInfo]::InvariantCulture
$palette = @{ Forest = '#163D31'; Ivory = '#F4F6E9'; Lime = '#C7EB91' }

function New-RoundedPath([single]$x, [single]$y, [single]$w, [single]$h, [single]$r) {
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $diameter = 2 * $r
    $path.AddArc($x, $y, $diameter, $diameter, 180, 90)
    $path.AddArc($x + $w - $diameter, $y, $diameter, $diameter, 270, 90)
    $path.AddArc($x + $w - $diameter, $y + $h - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($x, $y + $h - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return ,$path
}

function ConvertTo-PathData($path) {
    $parts = New-Object System.Collections.Generic.List[string]
    $points = $path.PathPoints
    $types = $path.PathTypes
    for ($i = 0; $i -lt $points.Length; $i++) {
        $kind = $types[$i] -band 7
        $point = $points[$i]
        $coordinates = $point.X.ToString('0.###', $invariant) + ' ' + $point.Y.ToString('0.###', $invariant)
        if ($kind -eq 0) { $parts.Add('M' + $coordinates) }
        elseif ($kind -eq 1) { $parts.Add('L' + $coordinates) }
        elseif ($kind -eq 3) {
            $coordinates += ' ' + $points[$i + 1].X.ToString('0.###', $invariant) + ' ' + $points[$i + 1].Y.ToString('0.###', $invariant)
            $coordinates += ' ' + $points[$i + 2].X.ToString('0.###', $invariant) + ' ' + $points[$i + 2].Y.ToString('0.###', $invariant)
            $parts.Add('C' + $coordinates)
            $i += 2
        }
        if (($types[$i] -band 128) -ne 0) { $parts.Add('Z') }
    }
    return $parts -join ''
}

$background = New-RoundedPath 0 0 96 96 24
$header = New-RoundedPath 20 22 38 16 4
$selected = New-RoundedPath 64 22 14 16 4
$stem = New-Object System.Drawing.Drawing2D.GraphicsPath
$stem.AddLine(40, 44, 56, 44)
$stem.AddLine(56, 44, 56, 63)
$stem.AddBezier(56, 63, 56, 64.6, 55.4, 66.1, 54.2, 67.2)
$stem.AddLine(54.2, 67.2, 43.4, 77.4)
$stem.AddBezier(43.4, 77.4, 42.1, 78.6, 40, 77.7, 40, 76)
$stem.CloseFigure()

$wordmark = New-Object System.Drawing.Drawing2D.GraphicsPath
$fontFamily = New-Object System.Drawing.FontFamily('Segoe UI')
$wordmark.AddString('torman', $fontFamily, [int][System.Drawing.FontStyle]::Bold, 76, [System.Drawing.PointF]::new(0, 0), [System.Drawing.StringFormat]::GenericTypographic)
$bounds = $wordmark.GetBounds()
$wordScale = 252 / $bounds.Width
$wordMatrix = [System.Drawing.Drawing2D.Matrix]::new($wordScale, 0, 0, $wordScale, (116 - $bounds.X * $wordScale), ((96 - $bounds.Height * $wordScale) / 2 - $bounds.Y * $wordScale - 1))
$wordmark.Transform($wordMatrix)
$wordMatrix.Dispose()
$fontFamily.Dispose()

$shapes = @(
    @{ Path = $background; Fill = $palette.Forest },
    @{ Path = $header; Fill = $palette.Ivory },
    @{ Path = $selected; Fill = $palette.Lime },
    @{ Path = $stem; Fill = $palette.Ivory }
)
$markPaths = ($shapes | ForEach-Object { '<path fill="' + $_.Fill + '" d="' + (ConvertTo-PathData $_.Path) + '"/>' }) -join ''
$wordPath = ConvertTo-PathData $wordmark
$markSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96" role="img" aria-label="Torman"><title>Torman</title>' + $markPaths + '</svg>'
[System.IO.File]::WriteAllText((Join-Path $brandDirectory 'mark.svg'), $markSvg, $utf8)
[System.IO.File]::WriteAllText((Join-Path $projectRoot 'static/favicon.svg'), $markSvg, $utf8)
foreach ($variant in @('dark', 'light')) {
    $wordColor = if ($variant -eq 'dark') { $palette.Forest } else { $palette.Ivory }
    $svg = '<svg xmlns="http://www.w3.org/2000/svg" width="384" height="96" viewBox="0 0 384 96" role="img" aria-label="Torman"><title>Torman</title>' + $markPaths + '<path fill="' + $wordColor + '" d="' + $wordPath + '"/></svg>'
    [System.IO.File]::WriteAllText((Join-Path $brandDirectory ('logo-' + $variant + '.svg')), $svg, $utf8)
}

function Draw-Logo($graphics, [single]$x, [single]$y, [single]$size, [bool]$withWordmark, [string]$wordColor) {
    $state = $graphics.Save()
    $graphics.TranslateTransform($x, $y)
    $graphics.ScaleTransform(($size / 96), ($size / 96))
    foreach ($shape in $shapes) {
        $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($shape.Fill))
        $graphics.FillPath($brush, $shape.Path)
        $brush.Dispose()
    }
    if ($withWordmark) {
        $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($wordColor))
        $graphics.FillPath($brush, $wordmark)
        $brush.Dispose()
    }
    $graphics.Restore($state)
}

function Save-LogoPng([string]$filename, [int]$size, [bool]$withWordmark = $false, [string]$wordColor = '#163D31') {
    $width = if ($withWordmark) { $size * 4 } else { $size }
    # Supersampling keeps the 16/24 px icon clean without changing the silhouette.
    $large = [System.Drawing.Bitmap]::new($width * 4, $size * 4, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($large)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.Color]::Transparent)
    Draw-Logo $graphics 0 0 ($size * 4) $withWordmark $wordColor
    $graphics.Dispose()
    $small = [System.Drawing.Bitmap]::new($width, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($small)
    $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $graphics.DrawImage($large, [System.Drawing.Rectangle]::new(0, 0, $width, $size))
    $small.Save($filename, [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose()
    $large.Dispose()
    $small.Dispose()
}

$sizes = @(16, 24, 32, 48, 64, 128, 256)
foreach ($size in $sizes) { Save-LogoPng (Join-Path $brandDirectory ('icon-' + $size + '.png')) $size }
Save-LogoPng (Join-Path $brandDirectory 'apple-touch-icon.png') 180
Save-LogoPng (Join-Path $brandDirectory 'mark.png') 512
Save-LogoPng (Join-Path $brandDirectory 'logo-dark.png') 72 $true $palette.Forest
Save-LogoPng (Join-Path $brandDirectory 'logo-light.png') 72 $true $palette.Ivory

# Windows supports PNG-backed ICO frames, including 256px Explorer thumbnails.
$icoStream = [System.IO.MemoryStream]::new()
$writer = [System.IO.BinaryWriter]::new($icoStream)
$writer.Write([uint16]0)
$writer.Write([uint16]1)
$writer.Write([uint16]$sizes.Length)
$offset = 6 + 16 * $sizes.Length
$frames = New-Object System.Collections.Generic.List[byte[]]
foreach ($size in $sizes) {
    $bytes = [System.IO.File]::ReadAllBytes((Join-Path $brandDirectory ('icon-' + $size + '.png')))
    $frames.Add($bytes)
    $dimension = if ($size -eq 256) { 0 } else { $size }
    $writer.Write([byte]$dimension)
    $writer.Write([byte]$dimension)
    $writer.Write([byte]0)
    $writer.Write([byte]0)
    $writer.Write([uint16]1)
    $writer.Write([uint16]32)
    $writer.Write([uint32]$bytes.Length)
    $writer.Write([uint32]$offset)
    $offset += $bytes.Length
}
foreach ($frame in $frames) { $writer.Write([byte[]]$frame) }
$writer.Flush()
[System.IO.File]::WriteAllBytes((Join-Path $brandDirectory 'torman.ico'), $icoStream.ToArray())
$writer.Dispose()
$icoStream.Dispose()

# A contact sheet for checking the light/dark wordmarks and small icon sizes.
$preview = [System.Drawing.Bitmap]::new(1000, 560)
$graphics = [System.Drawing.Graphics]::FromImage($preview)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.Clear([System.Drawing.ColorTranslator]::FromHtml('#F6F8F4'))
$brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#15392F'))
$graphics.FillRectangle($brush, 0, 0, 1000, 280)
$brush.Dispose()
Draw-Logo $graphics 72 76 124 $true $palette.Ivory
Draw-Logo $graphics 72 356 124 $true $palette.Forest
Draw-Logo $graphics 770 78 120 $false $palette.Ivory
$sampleX = 730
foreach ($size in @(16, 24, 32, 48)) {
    $sample = [System.Drawing.Image]::FromFile((Join-Path $brandDirectory ('icon-' + $size + '.png')))
    $graphics.DrawImageUnscaled($sample, $sampleX, (420 - $size / 2))
    $sampleX += $size + 18
    $sample.Dispose()
}
$preview.Save((Join-Path $brandDirectory 'preview.png'), [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$preview.Dispose()
foreach ($shape in $shapes) { $shape.Path.Dispose() }
$wordmark.Dispose()
Write-Output 'Brand assets generated in static/brand.'
