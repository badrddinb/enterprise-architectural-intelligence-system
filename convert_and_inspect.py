import aspose.cad as cad
from aspose.cad.imageoptions import CadRasterizationOptions, PdfOptions, PngOptions

# Load the DWG
img = cad.Image.load('Two-story-house-410202.dwg')

# Export to PDF for raster pipeline testing
raster = CadRasterizationOptions()
raster.page_width = 4961.0  # A1 at 300 DPI
raster.page_height = 3508.0
raster.draw_type = cad.fileformats.cad.CadDrawTypeMode.USE_OBJECT_COLOR

pdf_opts = PdfOptions()
pdf_opts.vector_rasterization_options = raster
img.save('Two-story-house-410202.pdf', pdf_opts)
print('PDF exported successfully')

# Check what entities the DWG actually contains
print(f"Image class: {type(img)}")
print(f"Is CAD image: {isinstance(img, cad.CadImage)}")

if isinstance(img, cad.CadImage):
    # Get entity data
    entities = []
    for entity in img.entities:
        etype = entity.type_name
        entities.append(etype)
    
    from collections import Counter
    counts = Counter(entities)
    print(f"\nDWG Entity counts ({len(entities)} total):")
    for etype, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {etype}: {count}")