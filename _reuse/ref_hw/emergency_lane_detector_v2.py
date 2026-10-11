"""
高速公路应急车道违章检测系统 v2.0
====================================
针对4K行车记录仪视频优化，专门检测占用应急车道的车辆并记录车牌号。

场景说明：
  - 三车道高速公路，4K宽屏视频(3840x1696)
  - 拍摄车辆在第三车道（最右侧行车道）
  - 拍摄车右侧为应急车道
  - 应急车道区域：画面x≥0.84（基于实际画面分析校准）

技术方案：
  1. 4K降采样到1080p进行车辆检测（加速4倍）
  2. YOLOv8s 检测车辆位置
  3. 应急车道区域判定（画面右侧x≥0.84）
  4. 在原图上裁剪车辆区域，HyperLPR3识别车牌
  5. 多帧确认+去重，避免误报

使用方法：
  python emergency_lane_detector_v2.py --source 视频.mp4

作者: AI Assistant
日期: 2026-04-11
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np


@dataclass
class ViolationRecord:
    """违章记录"""
    frame_idx: int
    timestamp: float
    plate_number: str
    plate_confidence: float
    plate_type: int
    vehicle_bbox: Tuple[int, int, int, int]  # 原图坐标
    plate_bbox: Tuple[int, int, int, int]    # 原图坐标
    snapshot_path: str = ""
    video_timecode: str = ""
    in_emergency_lane_frames: int = 0  # 在应急车道中出现的帧数


class EmergencyLaneDetectorV2:
    """高速公路应急车道违章检测器 v2.0"""

    # YOLOv8 COCO类别 - 车辆
    VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

    def __init__(
        self,
        yolo_model_path: str = "yolov8s.pt",
        device: str = "cpu",
        conf_threshold: float = 0.25,
        # 应急车道区域参数（基于画面分析校准）
        lane_start_x: float = 0.84,      # 应急车道左边界（占画面宽度比例）
        lane_end_x: float = 1.0,          # 应急车道右边界
        lane_top_y: float = 0.15,         # 应急车道上边界（忽略远处小目标）
        min_overlap_ratio: float = 0.25,   # 车辆与应急车道最小重叠比例
        min_plate_confidence: float = 0.4, # 最小车牌识别置信度
        cooldown_frames: int = 150,        # 同一车牌两次记录之间的最小帧间隔
        confirm_frames: int = 5,           # 需要连续在应急车道出现的帧数才确认违章
        # 4K优化
        detection_scale: float = 0.5,      # 检测时缩放比例（0.5 = 1920x848）
    ):
        self.device = device
        self.conf_threshold = conf_threshold
        self.lane_start_x = lane_start_x
        self.lane_end_x = lane_end_x
        self.lane_top_y = lane_top_y
        self.min_overlap_ratio = min_overlap_ratio
        self.min_plate_confidence = min_plate_confidence
        self.cooldown_frames = cooldown_frames
        self.confirm_frames = confirm_frames
        self.detection_scale = detection_scale

        # 加载YOLOv8
        print(f"[1/3] 加载YOLOv8模型: {yolo_model_path}")
        from ultralytics import YOLO
        self.yolo = YOLO(yolo_model_path)
        self.yolo.to(device)
        print(f"      ✓ YOLOv8加载完成 (设备: {device})")

        # 加载HyperLPR3
        print("[2/3] 加载HyperLPR3车牌识别模型...")
        import hyperlpr3 as lpr3
        self.plate_catcher = lpr3.LicensePlateCatcher(detect_level=lpr3.DETECT_LEVEL_HIGH)
        print("      ✓ HyperLPR3加载完成")

        # 违章记录
        self.violations: List[ViolationRecord] = []
        self.frame_count = 0
        self.last_record_frame: Dict[str, int] = {}  # 车牌号 -> 上次记录帧
        
        # 跟踪器：跟踪每个检测框在应急车道的连续出现次数
        # 使用简单的IoU匹配
        self.track_history: List[Dict] = []  # 每个track: {bbox, in_lane_count, last_frame}
        self.next_track_id = 0

        print("[3/3] 系统就绪 ✓")

    def _get_emergency_lane_region(self, width: int, height: int) -> Tuple[int, int, int, int]:
        """获取应急车道区域坐标"""
        x1 = int(width * self.lane_start_x)
        x2 = int(width * self.lane_end_x)
        y1 = int(height * self.lane_top_y)
        y2 = height
        return (x1, y1, x2, y2)

    def _compute_overlap_ratio(
        self,
        bbox: Tuple[int, int, int, int],
        region: Tuple[int, int, int, int]
    ) -> float:
        """计算bbox与区域的重叠比例（相对于bbox面积）"""
        bx1, by1, bx2, by2 = bbox
        rx1, ry1, rx2, ry2 = region

        ix1 = max(bx1, rx1)
        iy1 = max(by1, ry1)
        ix2 = min(bx2, rx2)
        iy2 = min(by2, ry2)

        if ix1 >= ix2 or iy1 >= iy2:
            return 0.0

        intersection = (ix2 - ix1) * (iy2 - iy1)
        bbox_area = max((bx2 - bx1) * (by2 - by1), 1)

        return intersection / bbox_area

    def _compute_iou(self, box1: Tuple, box2: Tuple) -> float:
        """计算两个框的IoU"""
        x1 = max(box1[0], box2[0])
        y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2])
        y2 = min(box1[3], box2[3])

        if x1 >= x2 or y1 >= y2:
            return 0.0

        intersection = (x2 - x1) * (y2 - y1)
        area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
        area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
        union = area1 + area2 - intersection

        return intersection / max(union, 1)

    def _update_tracks(self, detections: List[Tuple], lane_region: Tuple, frame_idx: int):
        """
        更新跟踪器，使用简单的IoU匹配
        
        Args:
            detections: [(bbox, class_id, conf), ...] 原图坐标
            lane_region: 应急车道区域
            frame_idx: 当前帧号
        """
        # 标记现有tracks为未匹配
        matched_tracks = set()
        matched_dets = set()

        # 对每个新检测，找最佳匹配的track
        for det_idx, (bbox, class_id, conf) in enumerate(detections):
            best_iou = 0.3  # 最小IoU阈值
            best_track_idx = -1

            for track_idx, track in enumerate(self.track_history):
                if track_idx in matched_tracks:
                    continue
                if frame_idx - track['last_frame'] > 30:  # 超过30帧未更新，放弃
                    continue

                iou = self._compute_iou(bbox, track['bbox'])
                if iou > best_iou:
                    best_iou = iou
                    best_track_idx = track_idx

            overlap = self._compute_overlap_ratio(bbox, lane_region)
            in_lane = overlap >= self.min_overlap_ratio

            if best_track_idx >= 0:
                # 更新已有track
                track = self.track_history[best_track_idx]
                track['bbox'] = bbox
                track['last_frame'] = frame_idx
                track['class_id'] = class_id
                track['conf'] = conf
                if in_lane:
                    track['in_lane_count'] += 1
                else:
                    track['in_lane_count'] = max(0, track['in_lane_count'] - 1)
                matched_tracks.add(best_track_idx)
                matched_dets.add(det_idx)
            else:
                # 创建新track
                self.track_history.append({
                    'track_id': self.next_track_id,
                    'bbox': bbox,
                    'class_id': class_id,
                    'conf': conf,
                    'in_lane_count': 1 if in_lane else 0,
                    'last_frame': frame_idx,
                    'first_frame': frame_idx,
                })
                self.next_track_id += 1

        # 清理过期的tracks
        self.track_history = [
            t for t in self.track_history
            if frame_idx - t['last_frame'] <= 60
        ]

    def _recognize_plate(self, frame: np.ndarray, vehicle_bbox: Tuple[int, int, int, int]) -> Optional[Tuple[str, float, int, Tuple]]:
        """
        识别车辆车牌（在原图上操作以保证清晰度）
        """
        x1, y1, x2, y2 = vehicle_bbox

        # 扩展边界确保车牌完整
        h, w = frame.shape[:2]
        pad_x = int((x2 - x1) * 0.1)
        pad_y = int((y2 - y1) * 0.15)  # 车牌在车头/车尾，多留上下空间
        cx1 = max(0, x1 - pad_x)
        cy1 = max(0, y1 - pad_y)
        cx2 = min(w, x2 + pad_x)
        cy2 = min(h, y2 + pad_y)

        vehicle_crop = frame[cy1:cy2, cx1:cx2]
        if vehicle_crop.size == 0:
            return None

        # 如果裁剪区域太大（4K），缩小到合理尺寸
        crop_h, crop_w = vehicle_crop.shape[:2]
        max_size = 800
        if max(crop_w, crop_h) > max_size:
            scale = max_size / max(crop_w, crop_h)
            new_w, new_h = int(crop_w * scale), int(crop_h * scale)
            vehicle_crop = cv2.resize(vehicle_crop, (new_w, new_h))

        # HyperLPR3识别
        try:
            results = self.plate_catcher(vehicle_crop)
            if results and len(results) > 0:
                best = None
                best_conf = 0
                for r in results:
                    plate_no = r[0]
                    conf = float(r[1])
                    plate_type = int(r[2])
                    plate_bbox_local = r[3]

                    # 过滤：车牌号至少7位，置信度足够
                    if conf > best_conf and len(plate_no) >= 7:
                        best_conf = conf
                        # 将局部坐标转换为全局坐标
                        # 如果做了缩放，需要还原
                        if max(crop_w, crop_h) > max_size:
                            scale_back = max(crop_w, crop_h) / max_size
                            global_plate_bbox = (
                                int(plate_bbox_local[0] * scale_back) + cx1,
                                int(plate_bbox_local[1] * scale_back) + cy1,
                                int(plate_bbox_local[2] * scale_back) + cx1,
                                int(plate_bbox_local[3] * scale_back) + cy1,
                            )
                        else:
                            global_plate_bbox = (
                                plate_bbox_local[0] + cx1,
                                plate_bbox_local[1] + cy1,
                                plate_bbox_local[2] + cx1,
                                plate_bbox_local[3] + cy1,
                            )
                        best = (plate_no, conf, plate_type, global_plate_bbox)

                return best
        except Exception as e:
            pass

        return None

    def _format_timecode(self, frame_idx: int, fps: float) -> str:
        """格式化时间码"""
        total_seconds = frame_idx / fps if fps > 0 else 0
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        frames = int((total_seconds % 1) * fps)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{frames:02d}"

    def process_video(
        self,
        source: str,
        output_path: Optional[str] = None,
        report_path: Optional[str] = None,
        snapshot_dir: Optional[str] = None,
        preview: bool = False,
        skip_frames: int = 2,  # 每隔几帧检测一次（加速处理）
    ) -> List[ViolationRecord]:
        """
        处理视频，检测应急车道违章

        Args:
            source: 视频文件路径
            output_path: 输出标注视频路径
            report_path: 报告输出路径
            snapshot_dir: 违章截图保存目录
            preview: 是否实时预览
            skip_frames: 跳帧检测（每N帧检测1次，加速处理）
        """
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise FileNotFoundError(f"无法打开视频: {source}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        print(f"\n视频信息: {width}x{height} @ {fps:.1f}fps, 共{total_frames}帧 ({total_frames/fps:.1f}秒)")

        # 应急车道区域（原图坐标）
        lane_region = self._get_emergency_lane_region(width, height)
        print(f"应急车道区域: x=[{lane_region[0]}, {lane_region[2]}] y=[{lane_region[1]}, {lane_region[3]}]")
        print(f"  占画面宽度: {self.lane_start_x*100:.0f}% ~ {self.lane_end_x*100:.0f}%")
        print(f"  跳帧: 每{skip_frames+1}帧检测1次")
        print(f"  确认帧数: 连续{self.confirm_frames}帧在应急车道才确认违章")

        # 降采样尺寸
        det_w = int(width * self.detection_scale)
        det_h = int(height * self.detection_scale)

        # 输出
        writer = None
        if output_path:
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
            print(f"输出视频: {output_path}")

        # 截图目录
        if snapshot_dir is None:
            snapshot_dir = os.path.join(os.path.dirname(os.path.abspath(source)), "violation_snapshots")
        os.makedirs(snapshot_dir, exist_ok=True)

        # 处理
        self.frame_count = 0
        self.violations = []
        start_time = time.time()

        # 记录已确认的违章track（避免重复记录同一辆车）
        confirmed_tracks: Set[int] = set()  # track_id set

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            self.frame_count += 1

            # 跳帧检测
            if self.frame_count % (skip_frames + 1) != 1 and self.frame_count > 1:
                if writer:
                    writer.write(frame)  # 原帧写入
                continue

            # 1. 降采样用于检测
            if self.detection_scale < 1.0:
                small_frame = cv2.resize(frame, (det_w, det_h))
            else:
                small_frame = frame

            # 2. YOLOv8 检测车辆
            results = self.yolo.predict(
                small_frame,
                conf=self.conf_threshold,
                device=self.device,
                classes=list(self.VEHICLE_CLASSES.keys()),
                verbose=False,
            )

            # 3. 将检测结果映射回原图坐标
            detections_orig = []
            if results[0].boxes is not None and len(results[0].boxes) > 0:
                scale_x = width / det_w
                scale_y = height / det_h
                for box in results[0].boxes:
                    class_id = int(box.cls[0])
                    sx1, sy1, sx2, sy2 = box.xyxy[0].cpu().numpy()
                    # 还原到原图坐标
                    x1 = int(sx1 * scale_x)
                    y1 = int(sy1 * scale_y)
                    x2 = int(sx2 * scale_x)
                    y2 = int(sy2 * scale_y)
                    conf = float(box.conf[0])
                    detections_orig.append(((x1, y1, x2, y2), class_id, conf))

            # 4. 更新跟踪器
            self._update_tracks(detections_orig, lane_region, self.frame_count)

            # 5. 检查跟踪器中是否有车辆在应急车道
            frame_violations = []
            for track in self.track_history:
                if track['in_lane_count'] >= self.confirm_frames:
                    track_id = track['track_id']
                    
                    # 如果这个track已经被记录过，跳过
                    if track_id in confirmed_tracks:
                        # 但检查是否超过冷却期
                        last_frame = self.last_record_frame.get(f"track_{track_id}", -9999)
                        if self.frame_count - last_frame < self.cooldown_frames:
                            continue
                    
                    # 6. 在原图上识别车牌
                    plate_result = self._recognize_plate(frame, track['bbox'])

                    if plate_result:
                        plate_no, plate_conf, plate_type, plate_bbox = plate_result

                        if plate_conf >= self.min_plate_confidence:
                            # 冷却期检查
                            last_frame = self.last_record_frame.get(plate_no, -9999)
                            if self.frame_count - last_frame >= self.cooldown_frames:
                                violation = ViolationRecord(
                                    frame_idx=self.frame_count,
                                    timestamp=self.frame_count / fps if fps > 0 else 0,
                                    plate_number=plate_no,
                                    plate_confidence=plate_conf,
                                    plate_type=plate_type,
                                    vehicle_bbox=track['bbox'],
                                    plate_bbox=plate_bbox,
                                    video_timecode=self._format_timecode(self.frame_count, fps),
                                    in_emergency_lane_frames=track['in_lane_count'],
                                )
                                frame_violations.append(violation)
                                self.last_record_frame[plate_no] = self.frame_count
                                self.last_record_frame[f"track_{track_id}"] = self.frame_count
                                confirmed_tracks.add(track_id)
                    else:
                        # 车在应急车道但未识别到车牌
                        # 仍然标记为疑似违章（在视频中标注）
                        if track_id not in confirmed_tracks:
                            # 记录为"未知车牌"违章
                            last_frame = self.last_record_frame.get(f"track_{track_id}", -9999)
                            if self.frame_count - last_frame >= self.cooldown_frames:
                                violation = ViolationRecord(
                                    frame_idx=self.frame_count,
                                    timestamp=self.frame_count / fps if fps > 0 else 0,
                                    plate_number="未识别",
                                    plate_confidence=0.0,
                                    plate_type=0,
                                    vehicle_bbox=track['bbox'],
                                    plate_bbox=track['bbox'],
                                    video_timecode=self._format_timecode(self.frame_count, fps),
                                    in_emergency_lane_frames=track['in_lane_count'],
                                )
                                frame_violations.append(violation)
                                self.last_record_frame[f"track_{track_id}"] = self.frame_count
                                confirmed_tracks.add(track_id)

            # 7. 保存违章截图
            for v in frame_violations:
                snap_filename = f"violation_{v.plate_number}_{v.frame_idx:06d}.jpg"
                snap_path = os.path.join(snapshot_dir, snap_filename)

                # 在截图上标注
                snap = frame.copy()
                vx1, vy1, vx2, vy2 = v.vehicle_bbox
                cv2.rectangle(snap, (vx1, vy1), (vx2, vy2), (0, 0, 255), 3)
                cv2.putText(snap, f"EMERGENCY LANE VIOLATION", (vx1, vy1 - 60),
                           cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
                cv2.putText(snap, f"Plate: {v.plate_number} ({v.plate_confidence:.2f})", (vx1, vy1 - 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                cv2.putText(snap, f"Time: {v.video_timecode}", (vx1, vy1 - 5),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

                # 裁剪车牌区域（放大5倍保存）
                px1, py1, px2, py2 = v.plate_bbox
                px1, py1 = max(0, px1), max(0, py1)
                px2, py2 = min(width, px2), min(height, py2)
                plate_crop = frame[py1:py2, px1:px2]
                if plate_crop.size > 0:
                    plate_h, plate_w = plate_crop.shape[:2]
                    # 放大车牌
                    plate_crop_big = cv2.resize(plate_crop, (plate_w * 5, plate_h * 5),
                                                interpolation=cv2.INTER_CUBIC)
                    # 在截图右上角显示车牌放大图
                    ph, pw = plate_crop_big.shape[:2]
                    y_offset = 10
                    x_offset = width - pw - 10
                    if y_offset + ph <= height and x_offset >= 0:
                        snap[y_offset:y_offset+ph, x_offset:x_offset+pw] = plate_crop_big
                        cv2.rectangle(snap, (x_offset-2, y_offset-2), (x_offset+pw+2, y_offset+ph+2), (255, 0, 0), 2)

                # 缩小保存（4K太大了）
                save_h = min(1080, snap.shape[0])
                save_w = int(snap.shape[1] * save_h / snap.shape[0])
                snap_save = cv2.resize(snap, (save_w, save_h))
                cv2.imwrite(snap_path, snap_save)
                v.snapshot_path = snap_path

            self.violations.extend(frame_violations)

            # 8. 绘制标注帧
            annotated = self._draw_annotations(frame, detections_orig, lane_region, frame_violations)

            if writer:
                writer.write(annotated)

            if preview:
                small_preview = cv2.resize(annotated, (960, 424))
                cv2.imshow("Emergency Lane Detector v2", small_preview)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

            # 进度
            if self.frame_count % 100 == 0:
                elapsed = time.time() - start_time
                speed = self.frame_count / elapsed if elapsed > 0 else 0
                eta = (total_frames - self.frame_count) / speed if speed > 0 else 0
                print(f"  帧 {self.frame_count}/{total_frames} "
                      f"({self.frame_count/total_frames*100:.1f}%) "
                      f"速度: {speed:.1f}fps "
                      f"ETA: {eta/60:.1f}min "
                      f"违章: {len(self.violations)} "
                      f"跟踪中: {len(self.track_history)}")

        # 清理
        cap.release()
        if writer:
            writer.release()
        if preview:
            cv2.destroyAllWindows()

        elapsed = time.time() - start_time
        print(f"\n处理完成! 耗时: {elapsed:.1f}s ({elapsed/60:.1f}min), 平均: {self.frame_count/elapsed:.1f}fps")
        print(f"共检测到 {len(self.violations)} 起应急车道违章")

        # 生成报告
        if report_path:
            self._generate_report(report_path, source, fps)

        return self.violations

    def _draw_annotations(
        self,
        frame: np.ndarray,
        detections: List[Tuple],
        lane_region: Tuple[int, int, int, int],
        frame_violations: List[ViolationRecord],
    ) -> np.ndarray:
        """绘制标注"""
        annotated = frame.copy()
        h, w = annotated.shape[:2]

        # 绘制应急车道区域（半透明红色）
        overlay = annotated.copy()
        lx1, ly1, lx2, ly2 = lane_region
        cv2.rectangle(overlay, (lx1, ly1), (lx2, ly2), (0, 0, 200), -1)
        cv2.addWeighted(overlay, 0.15, annotated, 0.85, 0, annotated)

        # 应急车道边界线（醒目白色虚线）
        cv2.line(annotated, (lx1, ly1), (lx1, ly2), (255, 255, 255), 3)
        # 画虚线效果
        for y in range(ly1, ly2, 40):
            cv2.line(annotated, (lx1-5, y), (lx1-5, min(y+20, ly2)), (0, 0, 255), 4)

        cv2.putText(annotated, "EMERGENCY LANE", (lx1 + 10, ly1 + 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        # 绘制车辆检测框
        for bbox, class_id, conf in detections:
            x1, y1, x2, y2 = bbox
            if class_id not in self.VEHICLE_CLASSES:
                continue

            overlap = self._compute_overlap_ratio(bbox, lane_region)
            is_in_lane = overlap >= self.min_overlap_ratio

            # 查找这个框的跟踪信息
            in_lane_count = 0
            for track in self.track_history:
                if self._compute_iou(bbox, track['bbox']) > 0.3:
                    in_lane_count = track['in_lane_count']
                    break

            if is_in_lane:
                color = (0, 0, 255)  # 红色
                thickness = 3
            elif in_lane_count > 0:
                color = (0, 165, 255)  # 橙色
                thickness = 2
            else:
                color = (0, 200, 0)  # 绿色
                thickness = 1

            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness)

            class_name = self.VEHICLE_CLASSES.get(class_id, "vehicle")
            label = f"{class_name} {conf:.2f}"
            if is_in_lane:
                label += f" [IN LANE! x{in_lane_count}]"
            elif in_lane_count > 0:
                label += f" [approaching x{in_lane_count}]"
            cv2.putText(annotated, label, (x1, y1 - 5),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        # 绘制违章标记
        for v in frame_violations:
            vx1, vy1, vx2, vy2 = v.vehicle_bbox
            # 闪烁效果
            if self.frame_count % 6 < 3:
                cv2.rectangle(annotated, (vx1 - 8, vy1 - 8), (vx2 + 8, vy2 + 8), (0, 0, 255), 4)

            # 车牌号标注
            plate_text = f"Plate: {v.plate_number} ({v.plate_confidence:.2f})"
            cv2.putText(annotated, plate_text, (vx1, vy2 + 35),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

            # 车牌框
            if v.plate_number != "未识别":
                px1, py1, px2, py2 = v.plate_bbox
                cv2.rectangle(annotated, (px1, py1), (px2, py2), (255, 0, 0), 2)

        # 顶部信息栏
        info_bar = annotated[:60, :]
        overlay_info = info_bar.copy()
        overlay_info[:] = (0, 0, 0)
        cv2.addWeighted(overlay_info, 0.6, info_bar, 0.4, 0, info_bar)
        annotated[:60, :] = info_bar

        cv2.putText(annotated, f"Emergency Lane Violation Detector v2.0", (10, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
        cv2.putText(annotated, f"Frame: {self.frame_count}  Violations: {len(self.violations)}", (10, 48),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

        unique_plates = len(set(v.plate_number for v in self.violations if v.plate_number != "未识别"))
        cv2.putText(annotated, f"Unique plates: {unique_plates}", (500, 48),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)

        return annotated

    def _generate_report(self, report_path: str, video_path: str, fps: float):
        """生成违章报告"""
        plate_violations = defaultdict(list)
        for v in self.violations:
            plate_violations[v.plate_number].append(v)

        report = {
            "video_path": video_path,
            "total_frames": self.frame_count,
            "total_violations": len(self.violations),
            "unique_plates": len([p for p in plate_violations if p != "未识别"]),
            "violations": [],
            "plates_summary": [],
        }

        for v in self.violations:
            report["violations"].append({
                "frame": v.frame_idx,
                "timecode": v.video_timecode,
                "timestamp_seconds": round(v.timestamp, 2),
                "plate_number": v.plate_number,
                "plate_confidence": round(v.plate_confidence, 3),
                "plate_type": v.plate_type,
                "vehicle_bbox": list(v.vehicle_bbox),
                "plate_bbox": list(v.plate_bbox),
                "in_emergency_lane_frames": v.in_emergency_lane_frames,
                "snapshot": v.snapshot_path,
            })

        for plate_no, viols in plate_violations.items():
            recognized_viols = [v for v in viols if v.plate_number != "未识别"]
            if recognized_viols:
                best = max(recognized_viols, key=lambda v: v.plate_confidence)
                report["plates_summary"].append({
                    "plate_number": plate_no,
                    "violation_count": len(viols),
                    "first_seen_frame": viols[0].frame_idx,
                    "first_seen_timecode": viols[0].video_timecode,
                    "best_confidence": best.plate_confidence,
                    "best_snapshot": best.snapshot_path,
                })
            else:
                report["plates_summary"].append({
                    "plate_number": "未识别",
                    "violation_count": len(viols),
                    "first_seen_frame": viols[0].frame_idx,
                    "first_seen_timecode": viols[0].video_timecode,
                    "best_confidence": 0.0,
                    "best_snapshot": viols[0].snapshot_path,
                })

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 打印摘要
        print("\n" + "=" * 60)
        print("  应急车道违章检测报告")
        print("=" * 60)
        print(f"视频: {video_path}")
        print(f"总帧数: {self.frame_count}")
        print(f"违章次数: {len(self.violations)}")
        
        recognized = [p for p in plate_violations if p != "未识别"]
        print(f"已识别车牌: {len(recognized)}")
        
        unrecognized = plate_violations.get("未识别", [])
        if unrecognized:
            print(f"未识别车牌车辆: {len(unrecognized)} 辆")
        print()

        if recognized:
            print("✅ 违章车牌列表:")
            print("-" * 60)
            for plate_no in sorted(recognized):
                viols = plate_violations[plate_no]
                best_conf = max(v.plate_confidence for v in viols)
                first_time = viols[0].video_timecode
                print(f"  🚗 {plate_no}  出现{len(viols)}次  "
                      f"首次: {first_time}  最高置信度: {best_conf:.2f}")

        if unrecognized:
            print("\n⚠️ 未识别车牌的违章车辆:")
            print("-" * 60)
            for v in unrecognized:
                print(f"  🚗 未识别  位置: {v.vehicle_bbox}  时间: {v.video_timecode}")

        if not recognized and not unrecognized:
            print("未检测到占用应急车道的车辆。")

        print()
        print(f"报告保存: {report_path}")
        if self.violations:
            print(f"截图目录: {os.path.dirname(self.violations[0].snapshot_path)}")

        return report


def main():
    parser = argparse.ArgumentParser(
        description="高速公路应急车道违章检测系统 v2.0",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--source", required=True, help="输入视频文件路径")
    parser.add_argument("--output", default="", help="输出标注视频路径")
    parser.add_argument("--model", default="", help="YOLOv8模型路径")
    parser.add_argument("--conf", type=float, default=0.25, help="YOLOv8置信度阈值 (默认0.25)")
    parser.add_argument("--device", default="cpu", help="推理设备 (默认cpu)")

    # 应急车道区域参数
    parser.add_argument("--lane-x", type=float, default=0.84,
                       help="应急车道左边界位置 (占画面宽度比例, 默认0.84)")
    parser.add_argument("--lane-width", type=float, default=0.16,
                       help="应急车道宽度 (占画面宽度比例, 默认0.16)")
    parser.add_argument("--lane-top", type=float, default=0.15,
                       help="应急车道上边界 (占画面高度比例, 默认0.15)")
    parser.add_argument("--overlap", type=float, default=0.25,
                       help="车辆与应急车道最小重叠比 (默认0.25)")
    parser.add_argument("--plate-conf", type=float, default=0.4,
                       help="车牌识别最低置信度 (默认0.4)")
    parser.add_argument("--cooldown", type=int, default=150,
                       help="同一车牌重复记录间隔帧数 (默认150帧=5秒@30fps)")
    parser.add_argument("--confirm", type=int, default=3,
                       help="确认违章所需连续帧数 (默认3)")
    parser.add_argument("--skip", type=int, default=2,
                       help="跳帧检测 (每N帧检测1次, 默认2, 即每3帧检测1次)")
    parser.add_argument("--scale", type=float, default=0.5,
                       help="检测降采样比例 (默认0.5=1080p)")
    parser.add_argument("--report", default="", help="报告输出路径")
    parser.add_argument("--preview", action="store_true", help="实时预览")

    args = parser.parse_args()

    # 模型路径
    model_path = args.model
    if not model_path:
        # 优先用yolov8s（更准确），回退到yolov8n
        ms_model_s = os.path.expanduser("~/.cache/modelscope/models/AI-ModelScope/YOLOv8/yolov8s.pt")
        ms_model_n = os.path.expanduser("~/.cache/modelscope/models/AI-ModelScope/YOLOv8/yolov8n.pt")
        if os.path.exists(ms_model_s):
            model_path = ms_model_s
        elif os.path.exists(ms_model_n):
            model_path = ms_model_n
        else:
            model_path = "yolov8s.pt"

    # 输出路径
    output_path = args.output
    if not output_path:
        base = os.path.splitext(os.path.abspath(args.source))[0]
        output_path = f"{base}_emergency_lane_detected.mp4"

    # 报告路径
    report_path = args.report
    if not report_path:
        base = os.path.splitext(os.path.abspath(args.source))[0]
        report_path = f"{base}_emergency_lane_report.json"

    print(f"模型: {model_path}")
    print(f"输入: {args.source}")
    print(f"输出: {output_path}")
    print(f"报告: {report_path}")

    # 创建检测器
    detector = EmergencyLaneDetectorV2(
        yolo_model_path=model_path,
        device=args.device,
        conf_threshold=args.conf,
        lane_start_x=args.lane_x,
        lane_end_x=args.lane_x + args.lane_width,
        lane_top_y=args.lane_top,
        min_overlap_ratio=args.overlap,
        min_plate_confidence=args.plate_conf,
        cooldown_frames=args.cooldown,
        confirm_frames=args.confirm,
        detection_scale=args.scale,
    )

    # 处理视频
    violations = detector.process_video(
        source=args.source,
        output_path=output_path,
        report_path=report_path,
        preview=args.preview,
        skip_frames=args.skip,
    )

    return violations


if __name__ == "__main__":
    main()
