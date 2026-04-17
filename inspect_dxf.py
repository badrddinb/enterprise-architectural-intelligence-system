from ezdxf import recover
import ezdxf

try:
    doc, auditor = recover.readfile('Two-story-house-410202.dxf')
    msp = doc.modelspace()
    lines = list(msp.query('LINE'))
    mtext = list(msp.query('MTEXT'))
    lwpoly = list(msp.query('LWPOLYLINE'))
    text = list(msp.query('TEXT'))
    circle = list(msp.query('CIRCLE'))
    arc = list(msp.query('ARC'))
    insert = list(msp.query('INSERT'))
    dim = list(msp.query('DIMENSION'))
    
    print(f"Entity counts:")
    print(f"  LINEs: {len(lines)}")
    print(f"  LWPOLYLINEs: {len(lwpoly)}")
    print(f"  MTEXTs: {len(mtext)}")
    print(f"  TEXTs: {len(text)}")
    print(f"  CIRCLEs: {len(circle)}")
    print(f"  ARCs: {len(arc)}")
    print(f"  INSERTs: {len(insert)}")
    print(f"  DIMENSIONs: {len(dim)}")
    
    if lines:
        print(f"\nFirst 3 LINE entities:")
        for l in lines[:3]:
            print(f"  start=({l.dxf.start.x:.2f}, {l.dxf.start.y:.2f}) end=({l.dxf.end.x:.2f}, {l.dxf.end.y:.2f})")
    
    if mtext:
        print(f"\nFirst 3 MTEXT contents:")
        for m in mtext[:3]:
            print(f"  {m.text[:80]}")
    
    if text:
        print(f"\nFirst 3 TEXT contents:")
        for t in text[:3]:
            print(f"  {t.dxf.text[:80]}")
    
    errors = auditor.errors
    print(f"\nRecovery errors: {len(errors)}")
    for e in errors[:5]:
        print(f"  {e}")
        
except Exception as ex:
    print(f"Error: {ex}")