import hid

for d in hid.enumerate():
    print(f"0x{d['vendor_id']:04x}:0x{d['product_id']:04x}  {d['product_string']}")
