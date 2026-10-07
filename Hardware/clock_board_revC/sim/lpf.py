import numpy as np
R0=50
def abcd_series(Z): return np.array([[1,Z],[0,1]],dtype=complex)
def abcd_shunt(Y): return np.array([[1,0],[Y,1]],dtype=complex)
def chain(f, C1,L2,C3,L4,C5, pad=True, QL=40, esr_c=0.05):
    w=2*np.pi*f
    def Lz(L): return 1j*w*L + w*L/QL
    def Cy(C): return 1/(1/(1j*w*C)+esr_c)
    M=np.eye(2,dtype=complex)
    M=M@abcd_series(1/(1j*w*100e-9))       # DC block 100 nF
    for el in [abcd_shunt(Cy(C1)),abcd_series(Lz(L2)),abcd_shunt(Cy(C3)),abcd_series(Lz(L4)),abcd_shunt(Cy(C5))]:
        M=M@el
    if pad:  # 3 dB pi pad: 294 shunt, 17.4 series, 294 shunt
        M=M@abcd_shunt(1/294)@abcd_series(17.4)@abcd_shunt(1/294)
    return M
def resp(M,Rs=50,RL=50):
    A,B,C,D=M[0,0],M[0,1],M[1,0],M[1,1]
    # voltage at load for source Vs=1 through Rs
    VL = RL/(A*RL+B+Rs*(C*RL+D))
    Zin=(A*RL+B)/(C*RL+D)
    return VL, Zin
def design(g,fc):
    wc=2*np.pi*fc
    return [g[0]/(wc*R0), g[1]*R0/wc, g[2]/(wc*R0), g[3]*R0/wc, g[4]/(wc*R0)]
bw=[0.618,1.618,2.0,1.618,0.618]
ch=[1.1468,1.3712,1.9750,1.3712,1.1468]  # Chebyshev 0.1 dB n=5
for name,g,fc in [("Butter14",bw,14e6),("Cheb0.1_12",ch,12e6),("Cheb0.1_13",ch,13e6)]:
    v=design(g,fc); print(name,["%.3g"%x for x in v])
def report(name,vals,pad=True):
    ref=0.5 # matched no-filter load voltage for Vs=1 into 50/50
    out=[]
    for f in [10e6,20e6,30e6,50e6,70e6,100e6]:
        VL,Zin=resp(chain(f,*vals,pad=pad))
        out.append("%dM:%.1fdB"%(f/1e6,20*np.log10(abs(VL)/ref)))
    VL,Zin=resp(chain(10e6,*vals,pad=pad))
    # power into 50 ohm with 3.3Vpp square source (fundamental amplitude 4/pi*1.65)
    Vs=4/np.pi*1.65
    P=(abs(VL)*Vs)**2/2/50
    VLhz,_=resp(chain(10e6,*vals,pad=pad),RL=1e6)
    print(name,"pad" if pad else "nopad"," ".join(out),"| Zin@10M=%.1f%+.1fj"%(Zin.real,Zin.imag),"| P50=%.1f dBm, Vpp50=%.2f, Vpp_hiZ=%.2f"%(10*np.log10(P/1e-3),2*abs(VL)*Vs,2*abs(VLhz)*Vs))
report("Butter14 std",[150e-12,1.0e-6,470e-12,1.0e-6,150e-12])
report("Butter14 std2",[150e-12,910e-9,470e-12,910e-9,150e-12])
report("Cheb12 std",[300e-12,910e-9,520e-12,910e-9,300e-12])
report("Cheb13 std",[270e-12,820e-9,470e-12,820e-9,270e-12])
report("Cheb13 std",[270e-12,820e-9,470e-12,820e-9,270e-12],pad=False)
rng=np.random.default_rng(1); r=[]
for i in range(5000):
    v=[270e-12*(1+rng.uniform(-.05,.05)),820e-9*(1+rng.uniform(-.05,.05)),470e-12*(1+rng.uniform(-.05,.05)),820e-9*(1+rng.uniform(-.05,.05)),270e-12*(1+rng.uniform(-.05,.05))]
    VL,_=resp(chain(10e6,*v)); r.append(20*np.log10(abs(VL)/0.5))
print("MC 10MHz loss min/max: %.2f %.2f"%(min(r),max(r)))
