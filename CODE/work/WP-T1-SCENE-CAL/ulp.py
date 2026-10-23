import math
w = 0.061401828952806735
e = 0.061401828952807144
d = abs(e-w)
ulp = math.ulp(w)
print(f"W={w!r}\nE={e!r}")
print(f"abs diff = {d:.3e}   ULP = {ulp:.3e}   diff = {d/ulp:.1f} ULP   relative = {d/w:.2e}")
print()
print("interpretation: two PHYSICALLY symmetric directions (E vs W mirror) score")
print("mathematically equal; the stored doubles differ by ~%.0f ULP, i.e. pure" % (d/ulp))
print("accumulation-order noise.  Whichever wins is decided by rounding.")
