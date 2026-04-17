import aspose.cad as cad

img = cad.Image.load('Two-story-house-410202.dwg')
opts = cad.imageoptions.CadRasterizationOptions()
opts.page_width = 4000.0
opts.page_height = 4000.0
dxf_opts = cad.imageoptions.DxfOptions()
dxf_opts.vector_rasterization_options = opts
img.save('Two-story-house-410202.dxf', dxf_opts)
print('Converted DWG to DXF successfully')