# SpatialFusion

检测框向内收缩 12%，提取深度 ROI，计算中位数、P10/P90、有效像素比例、局部一致性和当前图内百分位。输入带 `frame_id`，输出带 `unit: relative`。ROI 无足够有效像素时返回 `unknown`，不生成默认近距离值。
