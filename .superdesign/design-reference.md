# Superdesign 基准稿引用

- 项目 ID：`33dda45d-b694-44a8-80b5-d620b2c99dae`
- 稿件 ID：`651e3111-ab1b-471d-ac8a-ee2cfe27d5bc`
- 标题：A 股雷达 - 全部股票列表
- 预览地址：https://p.superdesign.dev/draft/651e3111-ab1b-471d-ac8a-ee2cfe27d5bc
- 画布地址：https://superdesign.dev/teams/6f4f5b9a-fc65-4103-8308-da02fafc49f1/projects/33dda45d-b694-44a8-80b5-d620b2c99dae

## 实现取舍

- 保留 208px 单菜单侧栏、高密度行情表、红涨绿跌与短价差标尺。
- 去掉基准稿中的外部字体请求，数字使用系统等宽字体回退，保证本地启动稳定。
- 概览区只展示后端能够真实返回的市场统计，不使用静态指数或虚构行情。
- 搜索、市场筛选和排序以真实接口能力为准，未实现能力不展示为可用控件。
- 小屏幕将侧栏收为顶部导航，行情表保持横向滚动。
