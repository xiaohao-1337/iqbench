"""Metadata shared by the board bridge and desktop control panel."""

from typing import NamedTuple


class Control(NamedTuple):
    label: str
    path: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[tuple[str, str], ...] = ()
    scale: float = 1


EXPOSURE = "/uapi/0/ae_uapi/expsw_attr"
EXPOSURE_INFO = "/uapi/0/ae_uapi/expinfo"


AE_CONTROLS = {
    "ae_mode": Control("曝光模式", EXPOSURE + "/AecOpType", choices=(
        ("RK_AIQ_OP_MODE_AUTO", "自动"), ("RK_AIQ_OP_MODE_MANUAL", "手动"))),
    "ae_time": Control("曝光时间 (ms)", EXPOSURE + "/stManual/LinearAE/TimeValue", 0.1, 30, 0.1, scale=1000),
    "ae_gain": Control("模拟增益 (×)", EXPOSURE + "/stManual/LinearAE/GainValue", 1, 31.6, 0.1),
}

EXPOSURE_IQ_CONTROLS = {
    "ae_ev": Control("曝光补偿 (%)", "/ae_calib/LinearAeCtrl/Evbias", -100, 100, 1),
    "ae_flicker": Control("防频闪", "/ae_calib/CommCtrl/AecAntiFlicker/enable"),
    "ae_flicker_hz": Control("防频闪频率", "/ae_calib/CommCtrl/AecAntiFlicker/Frequency", choices=(
        ("AECV2_FLICKER_FREQUENCY_50HZ", "50 Hz"), ("AECV2_FLICKER_FREQUENCY_60HZ", "60 Hz"))),
}

WB_MANUAL = "CALIB_WB_MODE_MANUAL"
WB_GAIN = "CALIB_MWB_MODE_WBGAIN"
WB_CCT = "CALIB_MWB_MODE_CCT"
WB_SCENE = "CALIB_MWB_MODE_SCENE"
WB_CONTROLS = {
    "wb_mode": Control("白平衡模式", "/wb_v21/control/mode", choices=(
        ("CALIB_WB_MODE_AUTO", "自动"), (WB_MANUAL, "手动"))),
    "wb_manual": Control("手动方式", "/wb_v21/manualPara/mode", choices=(
        (WB_CCT, "色温"), (WB_SCENE, "光源预设"), (WB_GAIN, "通道增益"))),
    "wb_temperature": Control("色温 (K)", "/wb_v21/manualPara/cfg/cct/CCT", 2500, 8000, 50),
    "wb_scene": Control("光源预设", "/wb_v21/manualPara/cfg/scene", choices=(
        ("CALIB_WB_SCENE_INCANDESCENT", "白炽灯"), ("CALIB_WB_SCENE_FLUORESCENT", "荧光灯"),
        ("CALIB_WB_SCENE_WARM_FLUORESCENT", "暖色荧光灯"), ("CALIB_WB_SCENE_DAYLIGHT", "日光"),
        ("CALIB_WB_SCENE_CLOUDY_DAYLIGHT", "阴天"), ("CALIB_WB_SCENE_TWILIGHT", "黄昏"),
        ("CALIB_WB_SCENE_SHADE", "阴影"))),
    **{f"wb_{key.lower()}": Control(f"{key} 增益 (×)", f"/wb_v21/manualPara/cfg/mwbGain/{index}", 0.5, 4, 0.02)
       for index, key in enumerate(("R", "Gr", "Gb", "B"))},
}

DETAIL_CONTROLS = {
    "bayer2d": Control("RAW 空域降噪", "/bayer2dnr_v2/TuningPara/enable"),
    "bayer3d": Control("RAW 时域降噪", "/bayertnr_v2/TuningPara/enable"),
    "ynr": Control("亮度降噪（YNR）", "/ynr_v3/TuningPara/enable"),
    "cnr": Control("色度降噪（CNR）", "/cnr_v2/TuningPara/enable"),
    "sharp": Control("锐化", "/sharp_v4/TuningPara/enable"),
    "dpcc": Control("坏点校正（DPCC）", "/adpcc_calib/DpccTuningPara/Enable"),
    "gic": Control("绿色不平衡校正（GIC）", "/agic_calib_v21/GicTuningPara/enable"),
}

TONE_CONTROLS = {
    "blc": Control("黑电平校正（BLC）", "/ablc_calib/BlcTuningPara/enable"),
    "lut3d": Control("3D LUT", "/lut3d_calib/common/enable"),
    "drc": Control("动态范围压缩（DRC）", "/adrc_calib_v11/DrcTuningPara/Enable"),
    "dehaze": Control("去雾／增强模块", "/adehaze_calib_v11/DehazeTuningPara/Enable"),
    "enhance": Control("细节增强", "/adehaze_calib_v11/DehazeTuningPara/enhance_setting/en"),
}

IQ_CONTROLS = {**EXPOSURE_IQ_CONTROLS, **WB_CONTROLS, **DETAIL_CONTROLS, **TONE_CONTROLS}
