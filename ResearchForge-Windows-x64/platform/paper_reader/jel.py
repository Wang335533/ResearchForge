"""Field vocabulary for the literature map: the top level of the JEL classification (American Economic
Association), in Chinese. One controlled list keeps the map from splitting the same field under several
names ("创新经济学", "技术创新研究", ...). A paper gets one primary field and at most one cross-field
placement in a different JEL field. Pure functions: no model, network or storage.
"""
import re

FIELDS = {
    'A': '经济学总论', 'B': '经济思想史与方法论', 'C': '计量与数理方法', 'D': '微观经济学',
    'E': '宏观与货币经济学', 'F': '国际经济学', 'G': '金融经济学', 'H': '公共经济学',
    'I': '健康、教育与福利', 'J': '劳动与人口经济学', 'K': '法与经济学', 'L': '产业组织',
    'M': '企业管理与会计', 'N': '经济史', 'O': '发展、创新与增长', 'P': '经济体制与政治经济学',
    'Q': '农业、资源与环境经济学', 'R': '城市、区域与交通经济学', 'Z': '其他专题',
}
# Second-level examples given to the model so topics stay comparable across papers (not exhaustive).
TOPIC_HINTS = ('G1 资产定价与金融市场', 'G2 金融机构与金融服务', 'G3 公司金融与公司治理', 'L1 市场结构与企业策略',
               'L2 企业目标、组织与行为', 'O3 创新、研发与技术变迁', 'O4 经济增长与全要素生产率', 'M4 会计与审计',
               'J2 劳动需求与就业', 'E4 货币与利率', 'F1 国际贸易', 'H2 税收与补贴', 'Q5 环境经济学', 'D8 信息与不确定性')
# Names the model or a person may use for a JEL field, tried in order; the first match wins.
KEYWORDS = (
    ('F', ('国际金融', '国际贸易')),
    ('C', ('计量', '数理方法', '统计方法', '方法论研究')),
    ('B', ('经济思想史', '方法论')),
    ('N', ('经济史', '历史')),
    ('K', ('法经济学', '法与经济', '法律')),
    ('M', ('会计', '审计', '管理学', '企业管理', '战略管理', '组织管理', '营销', '人力资源', '商学')),
    ('G', ('金融', '资产定价', '公司金融', '公司财务', '公司治理', '银行', '资本市场', '证券', '投资')),
    ('L', ('产业组织', '产业经济', '市场结构', '反垄断', '规制', '竞争政策', '行业竞争')),
    ('O', ('创新', '技术', '增长', '发展经济', '研发', '生产率', '数字经济')),
    ('J', ('劳动', '就业', '人口', '工资', '人力资本')),
    ('E', ('宏观', '货币', '通胀', '经济周期', '财政政策与宏观')),
    ('F', ('国际', '贸易', '汇率', '开放经济', '跨国')),
    ('H', ('公共', '财政', '税', '政府')),
    ('I', ('健康', '医疗', '教育', '福利', '卫生')),
    ('Q', ('环境', '能源', '资源', '农业', '气候', '碳')),
    ('R', ('城市', '区域', '房地产', '交通', '空间')),
    ('P', ('体制', '政治经济', '转型')),
    ('D', ('微观', '博弈', '决策', '行为经济', '契约', '信息经济')),
    ('A', ('经济学总论', '经济学教育')),
)
PLACEHOLDER = '待分类'


def code_of(name):
    """JEL letter for a field name like "O 发展、创新与增长", "O", "产业组织经济学" or "资产定价"; '' if unknown."""
    text = str(name or '').strip()
    letter = re.match(r'^([A-Z])(?:\d{0,2})?(?=[\s·.:：、-]|$)', text)
    if letter and letter[1] in FIELDS:
        return letter[1]
    for code, label in FIELDS.items():
        if text == label:
            return code
    for code, words in KEYWORDS:
        if any(w in text for w in words):
            return code
    return ''


def field_name(name):
    """Canonical Chinese field name, or the original text when it is not recognisably a JEL field."""
    code = code_of(name)
    return FIELDS[code] if code else str(name or '').strip()


def normalize_locations(locations, limit=2):
    """Canonical field names; one primary placement and at most one more in a different field."""
    result, seen = [], set()
    for loc in locations or []:
        if not isinstance(loc, dict):
            continue
        row = dict(loc)
        if row.get('field') and row['field'] != PLACEHOLDER:
            row['field'] = field_name(row['field'])
        if row.get('field') in seen:
            continue  # several topics in one field: the first (most central) one stays
        seen.add(row.get('field'))
        result.append(row)
        if len(result) >= limit:
            break
    return result


def prompt_list():
    return '；'.join(f'{code} {label}' for code, label in FIELDS.items())
