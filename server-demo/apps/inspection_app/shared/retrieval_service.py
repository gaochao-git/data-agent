"""
法律检索服务层
封装所有法律检索逻辑（6项核心检索）：
1. 违反条款
2. 处罚条款
3. 裁量基准
4. 整改措施
5. 处罚建议
6. 历史案例 ⭐ 新增

未来替换为真实 RAG 检索时，只需修改此文件
"""

from utils.logger import get_logger

logger = get_logger(__name__)


# ============ Mock 数据（未来替换为真实检索） ============

def _mock_legal_basis_retrieval(violation_summary: str) -> list[dict]:
    """Mock 违反条款检索"""
    return [
        {
            "law_name": "《非金融机构支付服务管理办法》",
            "law_item_name": "第三条",
            "law_item_content": "非金融机构提供支付服务，应当依据本办法规定取得《支付业务许可证》，成为支付机构。",
            "relevance_score": 0.95
        },
        {
            "law_name": "《价格法》",
            "law_item_name": "第十四条",
            "law_item_content": "经营者不得有下列不正当价格行为：利用虚假的或者使人误解的价格手段，诱骗消费者或者其他经营者与其进行交易。",
            "relevance_score": 0.88
        }
    ]


def _mock_penalty_clause_retrieval(violation_summary: str) -> list[dict]:
    """Mock 处罚条款检索"""
    return [
        {
            "law_name": "《非金融机构支付服务管理办法》",
            "law_item_name": "第四十二条",
            "law_item_content": "未经中国人民银行批准，擅自从事或变相从事支付业务的，由中国人民银行责令停止违法行为，没收违法所得，并处违法所得1倍以上5倍以下罚款；没有违法所得或者违法所得不足10万元的，处10万元以上50万元以下罚款。",
            "relevance_score": 0.92
        },
        {
            "law_name": "《价格违法行为行政处罚规定》",
            "law_item_name": "第七条",
            "law_item_content": "经营者违反价格法第十四条的规定，利用虚假的或者使人误解的价格手段，诱骗消费者或者其他经营者与其进行交易的，责令改正，没收违法所得，并处违法所得5倍以下的罚款；没有违法所得的，处5万元以上50万元以下的罚款。",
            "relevance_score": 0.90
        }
    ]


def _mock_discretion_standard_retrieval(violation_summary: str) -> list[str]:
    """Mock 裁量基准检索"""
    return [
        "违法所得不足10万元的，处10万元以上50万元以下罚款",
        "违法所得10万元以上的，处违法所得1倍以上5倍以下罚款",
        "情节严重的，可并处吊销支付业务许可证"
    ]


def _mock_rectification_measures_retrieval(violation_summary: str) -> list[str]:
    """Mock 整改措施检索"""
    return [
        "立即停止未经许可的支付业务活动",
        "注销或下架相关移动应用和服务",
        "配合监管部门调查，提供相关业务数据和资料",
        "加强内部合规管理，建立健全内部控制制度",
        "对相关责任人员进行问责和培训"
    ]


def _mock_penalty_suggestion_retrieval(violation_summary: str) -> list[str]:
    """Mock 处罚建议检索（可能来自历史案例）"""
    return [
        "责令立即停止违法行为",
        "没收违法所得",
        "根据违法所得金额，处以相应罚款（参考裁量基准）",
        "对相关责任人进行行政处罚",
        "将违法行为记入企业信用档案"
    ]


def _mock_historical_cases_retrieval(violation_summary: str) -> list[dict]:
    """Mock 历史案例检索 ⭐ 完整执法案例"""
    return [
        {
            # 基本信息
            "case_id": "京金检〔2023〕0301号",
            "case_title": "某科技公司未经许可从事支付业务案",
            "case_summary": "某科技公司在未取得支付业务许可证的情况下，通过其移动应用为用户提供资金转账和支付结算服务。",

            # 执法主体信息
            "inspected_person": "北京某某科技有限公司",
            "inspector": "张三、李四",
            "inspection_date": "2023年3月1日",
            "inspection_location": "北京市朝阳区某某大厦1001室",

            # 违法事实和法律依据
            "violation_facts": "该公司自2022年1月起，在其开发的\"某某支付\"移动应用中内置支付功能，允许用户通过该应用进行转账、缴费等支付业务。经查，该公司未取得《支付业务许可证》，违法从事支付业务活动，累计交易金额达5000余万元。",
            "violated_provisions": [
                {
                    "law_name": "非金融机构支付服务管理办法",
                    "law_item_name": "第三条",
                    "law_item_content": "非金融机构提供支付服务，应当依据本办法规定取得《支付业务许可证》，成为支付机构。"
                }
            ],
            "penalty_provisions": [
                {
                    "law_name": "非金融机构支付服务管理办法",
                    "law_item_name": "第四十二条",
                    "law_item_content": "未经中国人民银行批准，擅自从事或变相从事支付业务的，由中国人民银行责令停止违法行为，没收违法所得，并处违法所得1倍以上5倍以下罚款；没有违法所得或者违法所得不足10万元的，处10万元以上50万元以下罚款。"
                }
            ],

            # 裁量和处罚
            "discretionary_benchmarks": [
                "违法所得35万元，处以违法所得3倍罚款",
                "持续时间超过1年，属于情节严重",
                "主动配合调查，从轻处罚"
            ],
            "rectification_measures": [
                "立即停止支付业务活动",
                "下架\"某某支付\"应用",
                "对用户资金进行清退",
                "加强内部合规管理"
            ],
            "penalty_result": "责令停止违法行为，没收违法所得35万元，并处罚款105万元（违法所得的3倍）。",

            # 参考价值
            "reference_value": "本案与当前案件在违法行为性质（未经许可从事支付业务）、持续时间（超过1年）、涉案金额（百万级）等方面具有较高相似性，可作为裁量基准的重要参考。",
            "similarity_score": 0.92
        },
        {
            # 基本信息
            "case_id": "沪市监处〔2022〕1215号",
            "case_title": "某电商平台价格欺诈案",
            "case_summary": "某电商平台利用虚假的价格手段，诱导消费者购买商品。",

            # 执法主体信息
            "inspected_person": "上海某某电子商务有限公司",
            "inspector": "王五、赵六",
            "inspection_date": "2022年12月15日",
            "inspection_location": "上海市浦东新区某某科技园B栋5楼",

            # 违法事实和法律依据
            "violation_facts": "该平台在2022年\"双十一\"促销活动中，先将部分商品价格从599元提高至999元，再以\"双十一特价699元\"进行销售，实际成交价格高于平时价格，误导消费者认为享受了优惠，构成价格欺诈。涉及商品数量300余件，违法所得12万元。",
            "violated_provisions": [
                {
                    "law_name": "价格法",
                    "law_item_name": "第十四条",
                    "law_item_content": "经营者不得有下列不正当价格行为：（一）相互串通，操纵市场价格，损害其他经营者或者消费者的合法权益；（二）在依法降价处理鲜活商品、季节性商品、积压商品等商品外，为了排挤竞争对手或者独占市场，以低于成本的价格倾销，扰乱正常的生产经营秩序，损害国家利益或者其他经营者的合法权益；（三）捏造、散布涨价信息，哄抬价格，推动商品价格过高上涨的；（四）利用虚假的或者使人误解的价格手段，诱骗消费者或者其他经营者与其进行交易。"
                }
            ],
            "penalty_provisions": [
                {
                    "law_name": "价格违法行为行政处罚规定",
                    "law_item_name": "第七条",
                    "law_item_content": "经营者违反价格法第十四条的规定，利用虚假的或者使人误解的价格手段，诱骗消费者或者其他经营者与其进行交易的，责令改正，没收违法所得，并处违法所得5倍以下的罚款；没有违法所得的，处5万元以上50万元以下的罚款。"
                }
            ],

            # 裁量和处罚
            "discretionary_benchmarks": [
                "违法所得12万元，处以违法所得4倍罚款",
                "影响范围较大，涉及消费者众多",
                "及时改正，积极退款"
            ],
            "rectification_measures": [
                "下架涉案商品",
                "向受影响消费者退款",
                "在平台首页公开道歉",
                "完善价格管理制度"
            ],
            "penalty_result": "责令改正，没收违法所得12万元，并处罚款48万元（违法所得的4倍）。",

            # 参考价值
            "reference_value": "本案虽然涉及价格违法，但在裁量方法（以违法所得倍数确定罚款）、整改要求（公开道歉、完善制度）等方面可作为参考。",
            "similarity_score": 0.78
        }
    ]


# ============ 检索服务类 ============

class LegalRetrievalService:
    """
    法律检索服务

    提供统一的检索接口，封装 Mock/RAG 实现细节
    未来接入真实 RAG 时，只需修改此类的实现
    """

    def retrieve_legal_basis(self, violation_summary: str) -> list[dict]:
        """
        检索违反条款

        Args:
            violation_summary: 违法事实摘要

        Returns:
            违反条款列表，每项包含 law_name, law_item_name, law_item_content, relevance_score
        """
        logger.info("检索违反条款...")
        return _mock_legal_basis_retrieval(violation_summary)

    def retrieve_penalty_clause(self, violation_summary: str) -> list[dict]:
        """
        检索处罚条款

        Args:
            violation_summary: 违法事实摘要

        Returns:
            处罚条款列表，每项包含 law_name, law_item_name, law_item_content, relevance_score
        """
        logger.info("检索处罚条款...")
        return _mock_penalty_clause_retrieval(violation_summary)

    def retrieve_discretion_standard(self, violation_summary: str) -> list[str]:
        """
        检索裁量基准

        Args:
            violation_summary: 违法事实摘要

        Returns:
            裁量基准列表
        """
        logger.info("检索裁量基准...")
        return _mock_discretion_standard_retrieval(violation_summary)

    def retrieve_rectification_measures(self, violation_summary: str) -> list[str]:
        """
        检索整改措施

        Args:
            violation_summary: 违法事实摘要

        Returns:
            整改措施列表
        """
        logger.info("检索整改措施...")
        return _mock_rectification_measures_retrieval(violation_summary)

    def retrieve_penalty_suggestion(self, violation_summary: str) -> list[str]:
        """
        检索处罚建议

        Args:
            violation_summary: 违法事实摘要

        Returns:
            处罚建议列表
        """
        logger.info("检索处罚建议...")
        return _mock_penalty_suggestion_retrieval(violation_summary)

    def retrieve_historical_cases(self, violation_summary: str) -> list[dict]:
        """
        检索历史案例 ⭐ 新增

        Args:
            violation_summary: 违法事实摘要

        Returns:
            历史案例列表，每项包含 case_id, case_title, case_summary,
            violation_facts, penalty_result, reference_value, similarity_score
        """
        logger.info("检索历史案例...")
        return _mock_historical_cases_retrieval(violation_summary)

    def retrieve_all(self, violation_summary: str, include_cases: bool = True) -> dict:
        """
        一次性检索所有6项内容

        Args:
            violation_summary: 违法事实摘要
            include_cases: 是否包含历史案例（默认True）

        Returns:
            包含所有检索结果的字典
        """
        logger.info("批量检索所有法律依据和历史案例...")
        result = {
            "legal_basis": self.retrieve_legal_basis(violation_summary),
            "penalty_clause": self.retrieve_penalty_clause(violation_summary),
            "discretion_standard": self.retrieve_discretion_standard(violation_summary),
            "rectification_measures": self.retrieve_rectification_measures(violation_summary),
            "penalty_suggestion": self.retrieve_penalty_suggestion(violation_summary)
        }

        if include_cases:
            result["historical_cases"] = self.retrieve_historical_cases(violation_summary)

        return result


# ============ 全局服务实例 ============

# 创建全局单例，供所有节点使用
retrieval_service = LegalRetrievalService()
