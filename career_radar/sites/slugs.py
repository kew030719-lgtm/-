"""Chinese city name -> pinyin slug.

51job keys its job detail URLs on a city slug (`jobs.51job.com/beijing/<id>.html`)
rather than the numeric `jobArea` code used for search, and the slug follows the
*posting's* city, which can differ from the city searched.

Unknown cities fall back to the site's `/all/` route rather than guessing.
"""

CITY_SLUGS: dict[str, str] = {
    "北京": "beijing",
    "上海": "shanghai",
    "广州": "guangzhou",
    "深圳": "shenzhen",
    "杭州": "hangzhou",
    "成都": "chengdu",
    "武汉": "wuhan",
    "南京": "nanjing",
    "西安": "xian",
    "苏州": "suzhou",
    # Seen in real result sets alongside the ten above.
    "天津": "tianjin",
    "重庆": "chongqing",
    "长沙": "changsha",
    "郑州": "zhengzhou",
    "青岛": "qingdao",
    "宁波": "ningbo",
    "无锡": "wuxi",
    "合肥": "hefei",
    "福州": "fuzhou",
    "厦门": "xiamen",
    "济南": "jinan",
    "大连": "dalian",
    "沈阳": "shenyang",
    "哈尔滨": "haerbin",
    "昆明": "kunming",
    "南昌": "nanchang",
    "贵阳": "guiyang",
    "南宁": "nanning",
    "太原": "taiyuan",
    "石家庄": "shijiazhuang",
    "扬州": "yangzhou",
    "常州": "changzhou",
    "南通": "nantong",
    "徐州": "xuzhou",
    "温州": "wenzhou",
    "嘉兴": "jiaxing",
    "东莞": "dongguan",
    "佛山": "foshan",
    "珠海": "zhuhai",
    "中山": "zhongshan",
}