import numpy as np
import pandas as pd
import pythoncom
import win32com.client as win32
from win32com.client import VARIANT
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#User-Defined
hyFilePath = str(PROJECT_ROOT / "hysys" / "Simulation_Columns_Copy.hsc")
data_num     = 5000
rng_seed     = 39                  
alpha_totals = 0.6                  
alpha_within = 0.6                  
stream_name  = "IBPL"               
sheet_comp   = "Compositions"       
sheet_astm   = "ASTM-86-Temps"
sheet_mabp   = "MABP_D_M"


bounds_dict = {

    "CP4":   (0.010, 0.050),  # n-butane
    "CP5":   (0.030, 0.080),  # n-pentane
    "CP6":   (0.015, 0.030),  # n-hexane
    "CP7":   (0.015, 0.030),  # n-heptane

    "CI4":   (0.020, 0.050),  # isobutane
    "CI5":   (0.090, 0.120),  # isopentane
    "CI6_2": (0.030, 0.070),  # 2-methylpentane
    "CI6_3": (0.020, 0.050),  # 3-methylpentane
    "CI8":   (0.090, 0.140),  # 2,2,4-trimethylpentane (iso-octane)

    "CO4":   (0.010, 0.020),  # isobutene
    "CO5":   (0.030, 0.060),  # 1-pentene
    "CO5t":  (0.010, 0.040),  # trans-2-pentene

    "CN5":   (0.020, 0.040),  # cyclopentane
    "CN6":   (0.020, 0.040),  # cyclohexane
    "CN6m":  (0.030, 0.060),  # methylcyclopentane
    "CN7":   (0.030, 0.060),  # methylcyclohexane

    "CA6":   (0.003, 0.008),  # benzene (keep very low)
    "CA7":   (0.090, 0.180),  # toluene
    "CA8":   (0.010, 0.030),  # ethylbenzene
    "CA8x":  (0.050, 0.100),  # p-xylene
}


def project_capped_simplex(v, cap, R, tol=1e-12, max_iter=100):
    
    v   = np.asarray(v, dtype=float)
    cap = np.asarray(cap, dtype=float)
    if R <= tol:
        return np.zeros_like(v)
    lo, hi = np.min(v - cap), np.max(v)
    for _ in range(max_iter):
        tau = 0.5*(lo + hi)
        z   = np.clip(v - tau, 0.0, cap)
        s   = z.sum()
        if abs(s - R) <= tol:
            return z
        if s > R: lo = tau
        else:     hi = tau
    return np.clip(v - tau, 0.0, cap)

def sample_bounded_composition_grouped_dynamic(
    lower, upper, groups, rng,
    alpha_totals_range=(0.3, 1.3),     
    alpha_within_range=(0.4, 1.20),      
    cap_shrink_by_group=None,           
):
   
    l = np.asarray(lower, float)
    u = np.asarray(upper, float)
    cap = u - l

    if l.sum() > 1.0 + 1e-12:
        raise ValueError("Sum of lower bounds > 1 (infeasible).")
    if u.sum() < 1.0 - 1e-12:
        raise ValueError("Sum of upper bounds < 1 (infeasible).")

    
    alpha_totals = rng.uniform(*alpha_totals_range)
    alpha_within = rng.uniform(*alpha_within_range)

    
    cap_eff = cap.copy()
    if cap_shrink_by_group:
        group_names = ["P","I","O","N","A"]
        for gi, g_idx in enumerate(groups):
            gname = group_names[gi]
            if gname in cap_shrink_by_group:
                max_shrink = cap_shrink_by_group[gname]
                shrink_vec = rng.uniform(0.0, max_shrink, size=len(g_idx))
                cap_eff[g_idx] = np.maximum(0.0, cap[g_idx] - shrink_vec)

    Rtot = 1.0 - l.sum()
    if Rtot <= 1e-12:
        return l.copy()
    Lg = np.array([l[g].sum() for g in groups])
    Cg = np.array([cap_eff[g].sum() for g in groups])

    yg = rng.gamma(shape=alpha_totals, scale=1.0, size=len(groups))
    vg = Rtot * yg / yg.sum()
    zg = project_capped_simplex(v=vg, cap=Cg, R=Rtot)
    G  = Lg + zg

    x = l.copy()
    for gi, g in enumerate(groups):
        Rg = G[gi] - l[g].sum()
        if Rg <= 1e-12:
            continue
        yin = rng.gamma(shape=alpha_within, scale=1.0, size=len(g))
        vin = Rg * yin / yin.sum()
        xin = project_capped_simplex(v=vin, cap=cap_eff[g], R=Rg)
        x[g] += xin

    
    x[-1] = 1.0 - x[:-1].sum()
    return x



def open_hysys_case(path):
    print("Opening HYSYS case...")
    hyApp  = win32.Dispatch("HYSYS.Application")
    hyCase = hyApp.SimulationCases.Open(path)
    hyCase.visible = True
    print("HYSYS opened.")
    return hyCase

def main():
    rng = np.random.default_rng(rng_seed) if rng_seed is not None else np.random.default_rng()

    hyCase      = open_hysys_case(hyFilePath)
    flowsheet   = hyCase.Flowsheet
    material_streams = flowsheet.MaterialStreams
    solver      = hyCase.Solver

    sheet_comp_obj = flowsheet.Operations.Item(sheet_comp)
    sheet_astm_obj = flowsheet.Operations.Item(sheet_astm)
    sheet_mabp_obj = flowsheet.Operations.Item(sheet_mabp)


    comp_order = [sheet_comp_obj.Cell(0, j).CellText for j in range(20)] 

    try:
        lower = np.array([bounds_dict[name][0] for name in comp_order], dtype=float)
        upper = np.array([bounds_dict[name][1] for name in comp_order], dtype=float)
    except KeyError as e:
        raise KeyError(f"Component {e} not found in bounds_dict. Please add its bounds.")

    
    groups = [
        [i for i, n in enumerate(comp_order) if n.startswith("CP")],  # P
        [i for i, n in enumerate(comp_order) if n.startswith("CI")],  # I
        [i for i, n in enumerate(comp_order) if n.startswith("CO")],  # O
        [i for i, n in enumerate(comp_order) if n.startswith("CN")],  # N
        [i for i, n in enumerate(comp_order) if n.startswith("CA")],  # A
    ]
    
    assert lower.sum() <= 1.0 + 1e-12 and upper.sum() >= 1.0 - 1e-12, "Bounds infeasible."

    
    title_list = []
    for j in range(20):
        title_list.append(sheet_comp_obj.Cell(0, j).CellText)
    for j in range(11):
        title_list.append(sheet_astm_obj.Cell(0, j).CellText)
    for j in range(2):
        title_list.append(sheet_mabp_obj.Cell(0, j).CellText)

    
    IBPL_stream = material_streams[stream_name]
    all_rows = []
    num_failed = 0

    for n in range(data_num):
        try:
            
            comp_vec = sample_bounded_composition_grouped_dynamic(
            lower, upper, groups, rng,
            alpha_totals_range=(0.35, 1.0),
            alpha_within_range=(0.45, 1.1),
            cap_shrink_by_group={"P":0.01, "I":0.006, "O":0.006, "N":0.008, "A":0.01}
        )
            
            solver.CanSolve = False
            arr = comp_vec.astype(float)
            mole_fracs_variant = VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, arr)
            IBPL_stream.ComponentMolarFraction.SetValues(mole_fracs_variant)
            solver.CanSolve = True

            
            astm_vals = [sheet_astm_obj.Cell(1, j).CellValue for j in range(11)]
            mabp_vals = [sheet_mabp_obj.Cell(1, j).CellValue for j in range(2)]

            row = list(arr) + astm_vals + mabp_vals
            all_rows.append(row)

            if (n+1) % 25 == 0:
                print(f"Generated {n+1}/{data_num} samples.")
        except Exception as e:
            print(f"Skipping sample {n} due to error: {e}")
            num_failed += 1
            
            try:
                solver.CanSolve = True
            except Exception:
                pass
            continue

    print(f"Finished. Failed cases: {num_failed}")
    
    out_name = input("Enter output CSV name (without extension): ").strip() or "train"
    out_path = out_name + ".csv"
    df = pd.DataFrame(all_rows, columns=title_list)
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rows to {out_path}")

if __name__ == "__main__":
    main()
