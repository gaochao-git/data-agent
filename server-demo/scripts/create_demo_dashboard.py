"""
创建电商分析演示看板
"""
import asyncio
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apps.chat_data_app.services.dashboard_service import get_dashboard_service


async def create_ecommerce_dashboard(datasource_id: str, user_id: str = "default"):
    """创建电商分析看板"""
    service = get_dashboard_service()

    # 1. 创建看板
    dashboard = await service.create_dashboard(
        name="电商数据分析大屏",
        user_id=user_id,
        description="综合电商数据分析，包含销售、用户、商品等多维度指标",
        datasource_id=datasource_id,
        theme="light"
    )

    dashboard_id = dashboard["id"]
    print(f"✅ 创建看板成功: {dashboard['name']} (ID: {dashboard_id})")

    # 2. 添加面板
    panels = [
        # 第一行：核心指标卡片（4个小卡片）
        {
            "name": "订单总数",
            "sql": "SELECT COUNT(*) as value, '笔' as unit FROM orders",
            "chart_type": "number",
            "chart_config": {"color": "#1677FF", "prefix": "", "suffix": " 笔"},
            "layout_x": 0,
            "layout_y": 0,
            "layout_w": 3,
            "layout_h": 2
        },
        {
            "name": "商品总数",
            "sql": "SELECT COUNT(*) as value FROM products",
            "chart_type": "number",
            "chart_config": {"color": "#52C41A", "prefix": "", "suffix": " 个"},
            "layout_x": 3,
            "layout_y": 0,
            "layout_w": 3,
            "layout_h": 2
        },
        {
            "name": "用户总数",
            "sql": "SELECT COUNT(*) as value FROM users",
            "chart_type": "number",
            "chart_config": {"color": "#FA8C16", "prefix": "", "suffix": " 人"},
            "layout_x": 6,
            "layout_y": 0,
            "layout_w": 3,
            "layout_h": 2
        },
        {
            "name": "员工总数",
            "sql": "SELECT COUNT(*) as value FROM employees WHERE status = 1",
            "chart_type": "number",
            "chart_config": {"color": "#722ED1", "prefix": "", "suffix": " 人"},
            "layout_x": 9,
            "layout_y": 0,
            "layout_w": 3,
            "layout_h": 2
        },

        # 第二行：订单明细表 + 商品类别饼图
        {
            "name": "最新订单明细",
            "sql": """
                SELECT
                    oi.order_id as 订单编号,
                    p.name as 商品名称,
                    oi.quantity as 数量,
                    oi.unit_price as 单价,
                    oi.subtotal as 金额
                FROM order_items oi
                LEFT JOIN products p ON oi.product_id = p.product_id
                ORDER BY oi.order_id DESC
                LIMIT 20
            """,
            "chart_type": "table",
            "chart_config": None,
            "layout_x": 0,
            "layout_y": 2,
            "layout_w": 7,
            "layout_h": 4
        },
        {
            "name": "商品类别分布",
            "sql": """
                SELECT
                    c.category_name as category,
                    COUNT(*) as value
                FROM products p
                LEFT JOIN categories c ON p.category_id = c.category_id
                WHERE c.level = 1
                GROUP BY c.category_name
                ORDER BY value DESC
            """,
            "chart_type": "pie",
            "chart_config": {"radius": ["40%", "70%"]},
            "layout_x": 7,
            "layout_y": 2,
            "layout_w": 5,
            "layout_h": 4
        },

        # 第三行：订单数量柱状图 + 商品销量排行
        {
            "name": "每日订单数量趋势",
            "sql": """
                SELECT
                    order_date as date,
                    COUNT(*) as 订单数
                FROM orders
                GROUP BY order_date
                ORDER BY order_date DESC
                LIMIT 30
            """,
            "chart_type": "bar",
            "chart_config": {"color": "#1677FF"},
            "layout_x": 0,
            "layout_y": 6,
            "layout_w": 6,
            "layout_h": 4
        },
        {
            "name": "商品销量TOP10",
            "sql": """
                SELECT
                    p.name as 商品名称,
                    SUM(oi.quantity) as 销量
                FROM order_items oi
                LEFT JOIN products p ON oi.product_id = p.product_id
                GROUP BY p.name
                ORDER BY 销量 DESC
                LIMIT 10
            """,
            "chart_type": "bar",
            "chart_config": {"color": "#52C41A", "horizontal": True},
            "layout_x": 6,
            "layout_y": 6,
            "layout_w": 6,
            "layout_h": 4
        },

        # 第四行：部门统计 + 销售区域
        {
            "name": "部门预算统计",
            "sql": """
                SELECT
                    dept_name as 部门,
                    budget as 预算
                FROM departments
                ORDER BY budget DESC
            """,
            "chart_type": "bar",
            "chart_config": {"color": "#FA8C16"},
            "layout_x": 0,
            "layout_y": 10,
            "layout_w": 6,
            "layout_h": 4
        },
        {
            "name": "销售区域目标",
            "sql": """
                SELECT
                    region_name as 区域,
                    target_amount as 销售目标
                FROM sales_regions
                ORDER BY target_amount DESC
            """,
            "chart_type": "bar",
            "chart_config": {"color": "#722ED1"},
            "layout_x": 6,
            "layout_y": 10,
            "layout_w": 6,
            "layout_h": 4
        },

        # 第五行：用户地址分布
        {
            "name": "用户省份分布",
            "sql": """
                SELECT
                    province as category,
                    COUNT(*) as value
                FROM user_addresses
                WHERE is_default = 1
                GROUP BY province
                ORDER BY value DESC
                LIMIT 10
            """,
            "chart_type": "pie",
            "chart_config": None,
            "layout_x": 0,
            "layout_y": 14,
            "layout_w": 6,
            "layout_h": 4
        },
        {
            "name": "员工薪资分布",
            "sql": """
                SELECT
                    emp_name as 员工,
                    salary as 薪资
                FROM employees
                WHERE status = 1
                ORDER BY salary DESC
                LIMIT 10
            """,
            "chart_type": "bar",
            "chart_config": {"color": "#13C2C2"},
            "layout_x": 6,
            "layout_y": 14,
            "layout_w": 6,
            "layout_h": 4
        }
    ]

    # 添加所有面板
    for panel_data in panels:
        panel = await service.add_panel(
            dashboard_id=dashboard_id,
            name=panel_data["name"],
            sql=panel_data["sql"],
            chart_type=panel_data["chart_type"],
            chart_config=panel_data["chart_config"],
            layout_x=panel_data["layout_x"],
            layout_y=panel_data["layout_y"],
            layout_w=panel_data["layout_w"],
            layout_h=panel_data["layout_h"],
            datasource_id=datasource_id,
            user_id=user_id
        )
        print(f"  ✅ 添加面板: {panel['name']}")

    print(f"\n🎉 看板创建完成! 共 {len(panels)} 个面板")
    print(f"📊 看板ID: {dashboard_id}")
    return dashboard_id


async def create_sales_analysis_dashboard(datasource_id: str, user_id: str = "default"):
    """创建销售分析看板"""
    service = get_dashboard_service()

    dashboard = await service.create_dashboard(
        name="销售业绩分析",
        user_id=user_id,
        description="销售业绩、订单趋势、商品分析",
        datasource_id=datasource_id,
        theme="light"
    )

    dashboard_id = dashboard["id"]
    print(f"✅ 创建看板成功: {dashboard['name']} (ID: {dashboard_id})")

    panels = [
        # 销售概览
        {
            "name": "订单金额统计",
            "sql": """
                SELECT
                    SUM(total_amount) as value
                FROM orders
            """,
            "chart_type": "number",
            "chart_config": {"color": "#FF4D4F", "prefix": "¥", "suffix": ""},
            "layout_x": 0,
            "layout_y": 0,
            "layout_w": 4,
            "layout_h": 2
        },
        {
            "name": "平均订单金额",
            "sql": """
                SELECT
                    ROUND(AVG(total_amount), 2) as value
                FROM orders
            """,
            "chart_type": "number",
            "chart_config": {"color": "#1677FF", "prefix": "¥", "suffix": ""},
            "layout_x": 4,
            "layout_y": 0,
            "layout_w": 4,
            "layout_h": 2
        },
        {
            "name": "今日订单",
            "sql": """
                SELECT
                    COUNT(*) as value
                FROM orders
                WHERE order_date = CURRENT_DATE
            """,
            "chart_type": "number",
            "chart_config": {"color": "#52C41A", "prefix": "", "suffix": " 笔"},
            "layout_x": 8,
            "layout_y": 0,
            "layout_w": 4,
            "layout_h": 2
        },

        # 订单趋势折线图
        {
            "name": "订单趋势（近30天）",
            "sql": """
                SELECT
                    order_date as date,
                    COUNT(*) as 订单数,
                    SUM(total_amount) as 订单金额
                FROM orders
                GROUP BY order_date
                ORDER BY order_date DESC
                LIMIT 30
            """,
            "chart_type": "line",
            "chart_config": {"smooth": True},
            "layout_x": 0,
            "layout_y": 2,
            "layout_w": 8,
            "layout_h": 5
        },

        # 订单状态分布
        {
            "name": "订单状态分布",
            "sql": """
                SELECT
                    status as category,
                    COUNT(*) as value
                FROM orders
                GROUP BY status
            """,
            "chart_type": "pie",
            "chart_config": None,
            "layout_x": 8,
            "layout_y": 2,
            "layout_w": 4,
            "layout_h": 5
        },

        # 商品销售排行
        {
            "name": "热销商品TOP20",
            "sql": """
                SELECT
                    p.name as 商品,
                    SUM(oi.quantity) as 销量,
                    SUM(oi.subtotal) as 销售额
                FROM order_items oi
                LEFT JOIN products p ON oi.product_id = p.product_id
                GROUP BY p.name
                ORDER BY 销量 DESC
                LIMIT 20
            """,
            "chart_type": "table",
            "chart_config": None,
            "layout_x": 0,
            "layout_y": 7,
            "layout_w": 7,
            "layout_h": 5
        },

        # 类别销售占比
        {
            "name": "类别销售额占比",
            "sql": """
                SELECT
                    c.category_name as category,
                    SUM(oi.subtotal) as value
                FROM order_items oi
                LEFT JOIN products p ON oi.product_id = p.product_id
                LEFT JOIN categories c ON p.category_id = c.category_id
                WHERE c.level = 1
                GROUP BY c.category_name
                ORDER BY value DESC
            """,
            "chart_type": "pie",
            "chart_config": {"radius": ["50%", "80%"]},
            "layout_x": 7,
            "layout_y": 7,
            "layout_w": 5,
            "layout_h": 5
        }
    ]

    for panel_data in panels:
        panel = await service.add_panel(
            dashboard_id=dashboard_id,
            name=panel_data["name"],
            sql=panel_data["sql"],
            chart_type=panel_data["chart_type"],
            chart_config=panel_data["chart_config"],
            layout_x=panel_data["layout_x"],
            layout_y=panel_data["layout_y"],
            layout_w=panel_data["layout_w"],
            layout_h=panel_data["layout_h"],
            datasource_id=datasource_id,
            user_id=user_id
        )
        print(f"  ✅ 添加面板: {panel['name']}")

    print(f"\n🎉 看板创建完成! 共 {len(panels)} 个面板")
    return dashboard_id


async def create_hr_analysis_dashboard(datasource_id: str, user_id: str = "default"):
    """创建人力资源分析看板"""
    service = get_dashboard_service()

    dashboard = await service.create_dashboard(
        name="人力资源分析",
        user_id=user_id,
        description="员工、部门、薪资等人力资源数据分析",
        datasource_id=datasource_id,
        theme="light"
    )

    dashboard_id = dashboard["id"]
    print(f"✅ 创建看板成功: {dashboard['name']} (ID: {dashboard_id})")

    panels = [
        # 人员统计
        {
            "name": "在职员工数",
            "sql": "SELECT COUNT(*) as value FROM employees WHERE status = 1",
            "chart_type": "number",
            "chart_config": {"color": "#1677FF", "suffix": " 人"},
            "layout_x": 0,
            "layout_y": 0,
            "layout_w": 3,
            "layout_h": 2
        },
        {
            "name": "部门数量",
            "sql": "SELECT COUNT(*) as value FROM departments",
            "chart_type": "number",
            "chart_config": {"color": "#52C41A", "suffix": " 个"},
            "layout_x": 3,
            "layout_y": 0,
            "layout_w": 3,
            "layout_h": 2
        },
        {
            "name": "总薪资支出",
            "sql": "SELECT SUM(salary) as value FROM employees WHERE status = 1",
            "chart_type": "number",
            "chart_config": {"color": "#FA8C16", "prefix": "¥", "suffix": "/月"},
            "layout_x": 6,
            "layout_y": 0,
            "layout_w": 3,
            "layout_h": 2
        },
        {
            "name": "平均薪资",
            "sql": "SELECT ROUND(AVG(salary), 2) as value FROM employees WHERE status = 1",
            "chart_type": "number",
            "chart_config": {"color": "#722ED1", "prefix": "¥", "suffix": ""},
            "layout_x": 9,
            "layout_y": 0,
            "layout_w": 3,
            "layout_h": 2
        },

        # 部门分布
        {
            "name": "部门人员分布",
            "sql": """
                SELECT
                    d.dept_name as 部门,
                    COUNT(e.emp_id) as 人数
                FROM departments d
                LEFT JOIN employees e ON d.dept_id = e.dept_id AND e.status = 1
                GROUP BY d.dept_name
                ORDER BY 人数 DESC
            """,
            "chart_type": "bar",
            "chart_config": {"color": "#1677FF"},
            "layout_x": 0,
            "layout_y": 2,
            "layout_w": 6,
            "layout_h": 4
        },

        # 部门预算
        {
            "name": "部门预算分布",
            "sql": """
                SELECT
                    dept_name as category,
                    budget as value
                FROM departments
                ORDER BY budget DESC
            """,
            "chart_type": "pie",
            "chart_config": None,
            "layout_x": 6,
            "layout_y": 2,
            "layout_w": 6,
            "layout_h": 4
        },

        # 员工列表
        {
            "name": "员工详细信息",
            "sql": """
                SELECT
                    e.emp_name as 姓名,
                    d.dept_name as 部门,
                    e.position as 职位,
                    e.salary as 薪资,
                    e.hire_date as 入职日期,
                    CASE WHEN e.status = 1 THEN '在职' ELSE '离职' END as 状态
                FROM employees e
                LEFT JOIN departments d ON e.dept_id = d.dept_id
                ORDER BY e.salary DESC
            """,
            "chart_type": "table",
            "chart_config": None,
            "layout_x": 0,
            "layout_y": 6,
            "layout_w": 12,
            "layout_h": 5
        },

        # 薪资分布
        {
            "name": "薪资区间分布",
            "sql": """
                SELECT
                    position as 职位,
                    AVG(salary) as 平均薪资,
                    MIN(salary) as 最低薪资,
                    MAX(salary) as 最高薪资
                FROM employees
                WHERE status = 1
                GROUP BY position
                ORDER BY 平均薪资 DESC
            """,
            "chart_type": "bar",
            "chart_config": {"color": "#FA8C16"},
            "layout_x": 0,
            "layout_y": 11,
            "layout_w": 12,
            "layout_h": 4
        }
    ]

    for panel_data in panels:
        panel = await service.add_panel(
            dashboard_id=dashboard_id,
            name=panel_data["name"],
            sql=panel_data["sql"],
            chart_type=panel_data["chart_type"],
            chart_config=panel_data["chart_config"],
            layout_x=panel_data["layout_x"],
            layout_y=panel_data["layout_y"],
            layout_w=panel_data["layout_w"],
            layout_h=panel_data["layout_h"],
            datasource_id=datasource_id,
            user_id=user_id
        )
        print(f"  ✅ 添加面板: {panel['name']}")

    print(f"\n🎉 看板创建完成! 共 {len(panels)} 个面板")
    return dashboard_id


async def main():
    """主函数"""
    # 替换为你的数据源ID
    datasource_id = input("请输入Hive数据源ID（例如: ds_20260122084348_842320）: ").strip()

    if not datasource_id:
        print("❌ 数据源ID不能为空")
        return

    user_id = "default"

    print(f"\n开始创建演示看板...")
    print(f"数据源ID: {datasource_id}")
    print(f"用户ID: {user_id}")
    print("=" * 60)

    # 创建三个看板
    print("\n1️⃣  创建电商数据分析大屏...")
    await create_ecommerce_dashboard(datasource_id, user_id)

    print("\n" + "=" * 60)
    print("2️⃣  创建销售业绩分析...")
    await create_sales_analysis_dashboard(datasource_id, user_id)

    print("\n" + "=" * 60)
    print("3️⃣  创建人力资源分析...")
    await create_hr_analysis_dashboard(datasource_id, user_id)

    print("\n" + "=" * 60)
    print("✅ 所有看板创建完成！请在前端查看。")


if __name__ == "__main__":
    asyncio.run(main())
