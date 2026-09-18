from flask import Blueprint, render_template

history_bp = Blueprint('history', __name__, url_prefix='/history')

@history_bp.route('/')
def history():
    # 页面数据由前端 JS 调 /history?limit=&field= 接口获取（曲线 + 筛选 + 列表）
    return render_template('history.html')
