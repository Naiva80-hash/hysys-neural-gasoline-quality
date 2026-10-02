import asyncio
import json
import zipfile
import flet as ft
import numpy as np
import pandas as pd
import os, sys, traceback


Input_cols = ["IBP","10%L","20%L","30%L","40%L","50%L","60%L","70%L","80%L","90%L","FBP","MW","Density"]

COMP_BOUNDS = {
    "CP4": (0.010, 0.050), "CP5": (0.030, 0.080), "CP6": (0.015, 0.030), "CP7": (0.015, 0.030),
    "CI4": (0.020, 0.050), "CI5": (0.090, 0.120), "CI6_2": (0.030, 0.070), "CI6_3": (0.020, 0.050),
    "CI8": (0.090, 0.140), "CO4": (0.010, 0.020), "CO5": (0.030, 0.060), "CO5t": (0.010, 0.040),
    "CN5": (0.020, 0.040), "CN6": (0.020, 0.040), "CN6m": (0.030, 0.060), "CN7": (0.030, 0.060),
    "CA6": (0.003, 0.008), "CA7": (0.090, 0.180), "CA8": (0.010, 0.030), "CA8x": (0.050, 0.100),
}
PIONA_BOUNDS = {"P": (0.070, 0.190), "I": (0.250, 0.430), "O": (0.050, 0.120), "N": (0.100, 0.200), "A": (0.153, 0.318)}

def resource_path(rel_path: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel_path)


def load_resources():
    # Import TensorFlow in the worker too, so the form can appear immediately.
    import tensorflow as tf
    from tensorflow import keras

    class Sum_Layer(keras.layers.Layer):
        def call(self, inputs):
            return tf.reduce_sum(inputs, axis=-1, keepdims=True)

    class ComponentSlice(keras.layers.Layer):
        def __init__(self, start, stop, **kwargs):
            super().__init__(**kwargs)
            self.start = start
            self.stop = stop

        def call(self, inputs, mask=None):
            return inputs[:, self.start:self.stop]

        def get_config(self):
            return {**super().get_config(), "start": self.start, "stop": self.stop}

    df = pd.read_csv(resource_path("MinMax_train.csv")).set_index("Feature")
    col_min = df.loc[Input_cols, "Min"]
    col_max = df.loc[Input_cols, "Max"]
    model_path = resource_path("selus.keras")
    with zipfile.ZipFile(model_path) as archive:
        config = json.loads(archive.read("config.json"))

    # The bundled model's Lambda bytecode crashes on newer Python versions.
    # These are its five slices from First.ipynb (P, I, O, N, A).
    # Replace their configs before Keras can deserialize any saved bytecode.
    slices = {
        "lambda_85": (0, 4), "lambda_86": (4, 9),
        "lambda_87": (9, 12), "lambda_88": (12, 16),
        "lambda_89": (16, 20),
    }
    for layer in config["config"]["layers"]:
        if layer["class_name"] != "Lambda":
            continue
        name = layer["config"]["name"]
        if name not in slices:
            raise ValueError(f"Unsupported saved Lambda layer: {name}")
        start, stop = slices[name]
        layer["class_name"] = "ComponentSlice"
        layer["module"] = None
        layer["registered_name"] = "ComponentSlice"
        layer["config"] = {
            key: value for key, value in layer["config"].items()
            if key in ("name", "trainable", "dtype")
        }
        layer["config"].update(start=start, stop=stop)

    config.pop("compile_config", None)
    model = keras.models.model_from_json(
        json.dumps(config),
        custom_objects={"Sum_Layer": Sum_Layer, "ComponentSlice": ComponentSlice},
    )
    model.load_weights(model_path)
    return model, col_min, col_max

#Bounds
def bounds(lows, highs, tol_abs):
    lows_tol  = np.clip(lows - tol_abs,  0.0, 1.0)
    highs_tol = np.clip(highs + tol_abs, 0.0, 1.0)
    return lows_tol, highs_tol

def normalize_rows(df, col_min, col_max):
    return (df[Input_cols] - col_min) / (col_max - col_min)

def evaluate(y_pred, tol_abs):
    comp_names = list(COMP_BOUNDS.keys())
    piona_names = list(PIONA_BOUNDS.keys())
    # components
    Gc = y_pred[:, :20]
    lc = np.array([COMP_BOUNDS[c][0] for c in comp_names])
    hc = np.array([COMP_BOUNDS[c][1] for c in comp_names])
    lc, hc = bounds(lc, hc, tol_abs)
    mask_c = (Gc >= lc) & (Gc <= hc)
    status_c = {name: ("GOOD" if mask_c[0, i] else "BAD") for i, name in enumerate(comp_names)}
    overall_components = "GOOD" if mask_c.all() else "BAD"
    # PIONA
    Gp = y_pred[:, -5:]
    lp = np.array([PIONA_BOUNDS[g][0] for g in piona_names])
    hp = np.array([PIONA_BOUNDS[g][1] for g in piona_names])
    lp, hp = bounds(lp, hp, tol_abs)
    mask_p = (Gp >= lp) & (Gp <= hp)
    status_p = {name: ("GOOD" if mask_p[0, i] else "BAD") for i, name in enumerate(piona_names)}
    overall_piona = "GOOD" if mask_p.all() else "BAD"

    overall = "GOOD" if (overall_components == "GOOD" and overall_piona == "GOOD") else "BAD"
    return overall, status_c, status_p

#UI
async def main(page: ft.Page):
    page.title = "Gasoline Quality Checker"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 16
    page.window.width = 1100
    page.window.height = 760

    # header
    title = ft.Text("⛽ Gasoline Quality Checker", size=22, weight="bold")
    subtitle = ft.Text("A simple program for checking gasoline quality", color=ft.Colors.BLACK)

    # inputs
    inputs = {}
    controls_per_row = 2
    field_width = 420

    rows = []
    for name in Input_cols:
        t = ft.TextField(
            label=name,
            value="",
            border_radius=12,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=10),
            width=field_width
        )
        inputs[name] = t
        rows.append(ft.Container(t, padding=6))

    grid = [
        ft.Row(rows[i:i+controls_per_row], expand=True, alignment=ft.MainAxisAlignment.START, spacing=16)
        for i in range(0, len(rows), controls_per_row)
    ]

    inputs_card = ft.Card(
        ft.Container(ft.Column([ft.Text("Inputs", weight="bold"), *grid], spacing=8), padding=16),
        elevation=2
    )

    #Controls
    tol_slider = ft.Slider(min=0, max=20, divisions=20, value=5)
    tol_label = ft.Text("Tolerance: 0.005", weight="bold")

    def toast(msg: str, color=ft.Colors.RED_100):
        page.show_dialog(ft.SnackBar(content=ft.Text(msg), bgcolor=color))

    def tol_change(e):
        tol_label.value = f"Tolerance: {tol_slider.value/1000:.3f}"
        page.update()
    tol_slider.on_change = tol_change

    btn_predict = ft.Button(
        content="Predict & Check", icon=ft.Icons.PLAY_ARROW, disabled=True
    )
    status_text = ft.Text("Loading model…")
    progress = ft.ProgressBar()

    verdict_text = ft.Text("Overall: —", weight="bold", color=ft.Colors.BLACK, size=13)

    verdict_box = ft.Container(
    content=verdict_text,
    bgcolor=ft.Colors.GREY_200,                
    padding=ft.Padding.symmetric(vertical=6, horizontal=12),

    border_radius=20,
    alignment=ft.Alignment.CENTER_LEFT)
    bad_wrap  = ft.Row(wrap=True, spacing=6)
    good_wrap = ft.Row(wrap=True, spacing=6)

    # Populate these after the UI has been added to the page.
    model, col_min, col_max = None, None, None

    # set defaults so user sees something
    def set_defaults():
        if col_min is None:
            return
        for k in Input_cols:
            # Loading may finish after the user has already started typing.
            if inputs[k].value:
                continue
            try:
                inputs[k].value = f"{float(col_min[k]):.6f}"
            except Exception:
                inputs[k].value = ""
        page.update()

    def chips_for(status_dict, good=True):
        items = []
        for name, st in status_dict.items():
            if (good and st == "GOOD") or (not good and st == "BAD"):
                items.append(
                    ft.Container(
                    content=ft.Text(name, weight="bold", color=ft.Colors.WHITE, size=12),
                    bgcolor="#388e3c" if good else "#d32f2f",  # Green or Red
                    padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                    border_radius=20,
                    margin=ft.Margin.only(right=6, bottom=6),
                )
            )
        return items


    async def on_predict(e):
        if btn_predict.disabled:
            return
        if model is None or col_min is None or col_max is None:
            toast("Missing model or MinMax CSV", color=ft.Colors.AMBER_100)
            return

        # build input row
        try:
            row = {k: float(inputs[k].value) for k in Input_cols}
        except ValueError:
            toast("Please fill all inputs with valid numbers", color=ft.Colors.RED_100)
            return

        try:
            x = pd.DataFrame([row], columns=Input_cols)
            x_norm = normalize_rows(x, col_min, col_max).values.astype("float32")
        except Exception as ex:
            toast(f"Normalization error: {ex}", color=ft.Colors.RED_100)
            return

        btn_predict.disabled = True
        status_text.value = "Checking quality…"
        progress.visible = True
        tol = tol_slider.value/1000.0
        page.update()
        try:
            y = await asyncio.to_thread(model.predict, x_norm, verbose=0)
            overall, comp_status, piona_status = evaluate(y, tol)
        except Exception as ex:
            traceback.print_exc()
            status_text.value = f"Prediction failed: {ex}"
            toast(f"Model error: {ex}", color=ft.Colors.RED_100)
            return
        else:
            status_text.value = "Ready"
        finally:
            btn_predict.disabled = False
            progress.visible = False
            page.update()

        # verdict
        if overall == "GOOD":
            verdict_text.value = "Overall: GOOD"
            verdict_text.color = ft.Colors.WHITE
            verdict_box.bgcolor = "#388e3c"
        else:
            verdict_text.value = "Overall: BAD"
            verdict_text.color = ft.Colors.WHITE
            verdict_box.bgcolor = "#d32f2f"

        verdict_box.update()

       
        bad_wrap.controls.clear()
        good_wrap.controls.clear()

        bad_wrap.controls += chips_for(comp_status, good=False) + chips_for(piona_status, good=False)
        good_wrap.controls += chips_for(comp_status, good=True)  + chips_for(piona_status, good=True)
        


        page.update()

    btn_predict.on_click = on_predict

    controls_card = ft.Card(
    ft.Container(
        ft.Column(
            [
                ft.Text("Controls", weight="bold"),
                status_text, progress,
                tol_label, tol_slider,
                btn_predict,
                ft.Text("Verdict", weight="bold"),
                verdict_box,           
            ],
            spacing=10),
        padding=16),
    elevation=2
)

    page.add(
        ft.Column(
            [
                title, subtitle,
                ft.Row([inputs_card, controls_card], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, expand=True),
                ft.Text("Bad items (only show if any)", weight="bold"),
                ft.Card(ft.Container(bad_wrap, padding=12), elevation=1),
                ft.Text("Good items", weight="bold"),
                ft.Card(ft.Container(good_wrap, padding=12), elevation=1),
            ],
            spacing=14,
            expand=True
        )
    )

    # Yield the event loop while loading so Flet can render and handle events.
    try:
        model, col_min, col_max = await asyncio.to_thread(load_resources)
        set_defaults()
        status_text.value = "Ready"
        btn_predict.disabled = False
    except Exception as ex:
        traceback.print_exc()
        status_text.value = f"Could not load model or input ranges: {ex}"
        status_text.color = ft.Colors.RED
    finally:
        progress.visible = False
        page.update()

if __name__ == "__main__":
    # Tip: keep console ON while debugging
    ft.run(main)
