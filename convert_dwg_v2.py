import aspose.cad as cad
from aspose.cad.imageoptions import CadRasterizationOptions, DxfOptions, PdfOptions, PngOptions

# Load the DWG
img = cad.Image.load('Two-story-house-410202.dwg')

# Try simple DXF export without rasterization options
dxf_opts = DxfOptions()
img.save('Two-story-house-410202_v2.dxf', dxf_opts)
print('DXF v2 saved')

# Verify with ezdxf
from ezdxf import recover
doc, auditor = recover.readfile('Two-story-house-410202_v2.dxf')
msp = doc.modelspace()
print(f"LINEs: {len(list(msp.query('LINE')))}")
print(f"LWPOLYLINEs: {len(list(msp.query('LWPOLYLINE')))}")
print(f"MTEXTs: {len(list(msp.query('MTEXT')))}")
print(f"TEXTs: {len(list(msp.query('TEXT')))}")
print(f"Recovery errors: {len(auditor.errors)}")