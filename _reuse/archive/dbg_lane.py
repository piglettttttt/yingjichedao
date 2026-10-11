import json, sys, yaml
sys.path.insert(0, r"_reuse")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from stage2_fuse import point_in_polygon, vehicle_footprint, lane_overlap_ratio
cfg=yaml.safe_load(open(r"_reuse\config_roi.yaml",encoding="utf-8"))
el=cfg["emergency_lane"]; poly=[tuple(p) for p in el["polygon"]]
up=el["keypoint"]["up_ratio"]; wm=el["vehicle_width_from_plate"]
rows=[json.loads(l) for l in open(r"_reuse\run\cands_roi.jsonl",encoding="utf-8")]
for t in (2.47, 10.6, 15.0):
    near=[r for r in rows if abs(r["t"]-t)<=0.04]
    print("--- t=%.2f ---" % t)
    for r in near:
        (xa,xb),yg = vehicle_footprint(r, up, wm)
        ov = lane_overlap_ratio(r, poly, None, up, wm)
        # ROI 边界在该 y 处的 x
        (x1,y1),(x2,y2)=poly[0],poly[1]
        xb_line = x1 + (yg-y1)*(x2-x1)/(y2-y1)
        print("  %-10s plate_box=[%.0f,%.0f,%.0f,%.0f] w=%.0f" % (r["text"], *r["box"], r["w"]))
        print("      车体水平区间=[%.0f,%.0f] 着地y=%.0f  ROI斜边x=%.0f  overlap=%.2f" % (xa,xb,yg,xb_line,ov))
