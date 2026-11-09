"""Metadata shared by the board bridge and desktop control panel."""

from typing import NamedTuple


class Control(NamedTuple):
    label: str
    path: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[tuple[int | str, str], ...] = ()
    scale: float = 1

    @property
    def switch(self):
        return self.step is None and not self.choices


CPROC = "/cproc/param"
GAMMA_PATH = "/agamma_calib_v11/GammaTuningPara"
GAMMA_CURVE = f"{GAMMA_PATH}/Gamma_curve"
GRAY_CPSL, GRAY_OFF, GRAY_ON = range(3)
COLORS = {key: Control(label, f"{CPROC}/{key}", 0, 255, 1) for key, label in (
    ("brightness", "亮度"), ("contrast", "对比度"), ("saturation", "饱和度"), ("hue", "色相"),
)}
MODES = {
    "gray": Control("黑白模式", "/colorAsGrey/param/enable", choices=((GRAY_OFF, "关闭"), (GRAY_ON, "开启"))),
    "enable": Control("色彩处理（CPROC）", f"{CPROC}/enable"),
}
GAMMA = {
    "gamma_enable": Control("启用 Gamma", f"{GAMMA_PATH}/Gamma_en"),
    "gamma_offset": Control("输出偏移", f"{GAMMA_PATH}/Gamma_out_offset", 0, 4095, 1),
}

EXPOSURE = "/uapi/0/ae_uapi/expsw_attr"
EXPOSURE_INFO = "/uapi/0/ae_uapi/expinfo"
WORK_MODE = "/uapi/0/system/work_mode"
MANUAL_EXPOSURE = "RK_AIQ_OP_MODE_MANUAL"
EXPOSURE_FRAMES = {
    "RK_AIQ_WORKING_MODE_NORMAL": 1,
    "RK_AIQ_WORKING_MODE_ISP_HDR2": 2,
    "RK_AIQ_WORKING_MODE_ISP_HDR3": 3,
}

AE_GROUPS = (
    ("LinearAE", ("ae_time",), ("ae_gain",)),
    ("HdrAE", tuple(f"hdr{frame}_time" for frame in range(1, 4)),
     tuple(f"hdr{frame}_gain" for frame in range(1, 4))),
)


def manual_controls():
    for group, times, gains in AE_GROUPS:
        for frame, (time, gain) in enumerate(zip(times, gains)):
            prefix = "" if group == "LinearAE" else f"HDR 帧 {frame + 1} · "
            index = "" if group == "LinearAE" else f"/{frame}"
            path = f"{EXPOSURE}/stManual/{group}"
            yield time, Control(prefix + "曝光时间 (ms)", f"{path}/TimeValue{index}", step=0.001, scale=1000)
            yield gain, Control(prefix + "模拟增益 (×)", f"{path}/GainValue{index}", step=0.001)


LIVE_AE_CONTROLS = {
    "ae_mode": Control("曝光模式", EXPOSURE + "/AecOpType", choices=(
        ("RK_AIQ_OP_MODE_AUTO", "自动"), (MANUAL_EXPOSURE, "手动"))),
    **dict(manual_controls()),
}

# Older desktops construct the linear sliders from these bounds. Live validation uses device ranges.
AE_CONTROLS = {
    **LIVE_AE_CONTROLS,
    "ae_time": LIVE_AE_CONTROLS["ae_time"]._replace(minimum=0.1, maximum=30, step=0.1),
    "ae_gain": LIVE_AE_CONTROLS["ae_gain"]._replace(minimum=1, maximum=31.6, step=0.1),
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

IQ_CONTROLS = {**COLORS, "enable": MODES["enable"], **GAMMA, **EXPOSURE_IQ_CONTROLS,
               **WB_CONTROLS, **DETAIL_CONTROLS, **TONE_CONTROLS}
PARAMETERS = {**MODES, **IQ_CONTROLS, **LIVE_AE_CONTROLS}
