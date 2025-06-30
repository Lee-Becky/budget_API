# budget_optimizer_api.py
# A simple Flask API to act as a custom tool for Dify Agent.
#
# To run this locally:
# 1. pip install Flask pandas
# 2. python budget_optimizer_api.py
#
# To deploy:
# You can deploy this file to platforms like Vercel, Railway, or any Python hosting service.

from flask import Flask, request, jsonify
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

app = Flask(__name__)

def get_adjustment_percentage_rules(daily_spend):
    """
    根据PDF中的“调整幅度控制原则”返回调整上限和下限。
    Implements the "Adjustment Magnitude Control Principle" from the PDF.
    """
    if daily_spend <= 50:
        return 2.0  # 增加200%
    elif 50 < daily_spend <= 100:
        return 1.0  # 增加100%
    elif 100 < daily_spend <= 500:
        return 0.5  # 增加50%
    else:
        return 0.3  # 增加30%

def analyze_campaigns(total_budget, target_roas, campaign_data):
    """
    核心分析逻辑。
    Core analysis logic based on the provided PDF rules.
    """
    if not campaign_data:
        return {"error": "Campaign data is empty."}

    # 将输入的JSON数据转换为Pandas DataFrame以便于分析
    df = pd.DataFrame(campaign_data)

    # --- 数据预处理和指标计算 ---
    # 确保数值类型正确
    numeric_cols = ['cost', 'impression', 'click', 'action', 'purchase', 'purchase_value']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # 计算核心指标
    df['roas'] = df.apply(lambda row: row['purchase_value'] / row['cost'] if row['cost'] > 0 else 0, axis=1)
    df['cpc'] = df.apply(lambda row: row['cost'] / row['click'] if row['click'] > 0 else np.inf, axis=1)
    df['cpa'] = df.apply(lambda row: row['cost'] / row['action'] if row['action'] > 0 else np.inf, axis=1)
    
    # 将 create_time 转换为 datetime 对象
    df['create_time_dt'] = pd.to_datetime(df['create_time'], errors='coerce')
    
    # 获取当前时间（模拟）
    now = datetime.now()

    results = []
    
    # --- 遍历每个Campaign应用规则 ---
    for _, row in df.iterrows():
        campaign_id = row['campaign_id']
        current_budget = row['cost']
        roas = row['roas']
        cpc = row['cpc']
        create_time = row['create_time_dt']
        
        # 默认不调整
        new_budget = current_budget
        reason = "表现平稳或不满足调整条件，维持预算。"
        status = "unchanged"

        # --- 规则应用 ---
        # 规则 1: 新广告系列 (24小时内创建)
        if pd.notna(create_time) and (now - create_time) < timedelta(hours=24):
            avg_cpc_all = df[df['cpc'] != np.inf]['cpc'].mean()
            if cpc > avg_cpc_all * 1.3:
                new_budget = round(max(current_budget * 0.5, 1)) # 预算最大降幅50%，最低不低于1
                reason = f"新广告(24h内): CPC ({cpc:.2f}) 高于平均值 ({avg_cpc_all:.2f}) 的30%，降低预算50%。"
                status = "decreased"
            else:
                reason = "新广告(24h内): 表现正常，维持预算观察。"
                status = "unchanged"

        # 规则 2: 基于 ROAS 的优胜劣汰
        else:
            target_roas_value = target_roas / 100.0
            if roas > target_roas_value * 1.2: # ROAS 表现优秀 (高于目标20%)
                max_increase_factor = get_adjustment_percentage_rules(current_budget)
                increase_amount = current_budget * max_increase_factor
                new_budget = round(current_budget + increase_amount)
                reason = f"ROAS表现出色({roas:.2f})，高于目标({target_roas_value:.2f})。根据其消耗水平，预算上限可增加{max_increase_factor*100}%，增加预算至 {new_budget}。"
                status = "increased"
            elif 0 < roas < target_roas_value * 0.8: # ROAS 表现不佳 (低于目标20%)
                new_budget = round(max(current_budget * 0.7, 1)) # 削减30%预算
                reason = f"ROAS表现不佳({roas:.2f})，低于目标({target_roas_value:.2f})。削减预算30%。"
                status = "decreased"
        
        # 规则 3: 预算调整绝对值过小则不调整
        if abs(new_budget - current_budget) < 5:
            new_budget = current_budget
            if status != "unchanged": # 如果之前有过调整决策
                 reason += " 但因调整金额小于$5，故维持不变。"
            status = "unchanged"
            
        # 规则 4: 调整后预算必须为整数
        new_budget = int(round(new_budget))
        
        # 规则 5: 如果调整后预算低于初始预算20%或低于1美金，则考虑关闭
        # 在这个版本中我们先标记出来，而不是直接关闭
        if new_budget < max(current_budget * 0.2, 1) and status == "decreased":
             reason += " 预算已非常低，建议观察后考虑暂停。"


        results.append({
            "campaign_id": str(campaign_id),
            "old_budget": float(current_budget),
            "new_budget": float(new_budget),
            "adjustment_amount": float(new_budget - current_budget),
            "adjustment_percent": round((new_budget - current_budget) / current_budget * 100, 2) if current_budget > 0 else 0,
            "status": status,
            "reason": reason,
            "key_metric_value": f"ROAS: {roas:.2f}"
        })

    return results

@app.route('/analyze_budget', methods=['POST'])
def analyze_budget_endpoint():
    """
    API endpoint that the Dify agent will call.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        total_budget = data.get('total_budget')
        target_roas = data.get('target_roas')
        campaign_data = data.get('campaign_data')

        if not all([total_budget, target_roas, campaign_data]):
            return jsonify({"error": "Missing required parameters: total_budget, target_roas, campaign_data"}), 400

        # 调用核心分析函数
        analysis_result = analyze_campaigns(total_budget, target_roas, campaign_data)
        
        # 这里可以加入更多逻辑，例如确保调整后的总预算不超过 total_budget
        
        return jsonify(analysis_result)

    except Exception as e:
        # 在生产环境中，应该使用更完善的日志记录
        print(f"An error occurred: {e}")
        return jsonify({"error": "An internal error occurred.", "details": str(e)}), 500

if __name__ == '__main__':
    # For local testing, run on port 5001
    app.run(debug=True, port=5001)

