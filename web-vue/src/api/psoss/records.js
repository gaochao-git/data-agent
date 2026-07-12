// 获取用户所属机构列表
export function getUserOrgList() {
  return Promise.resolve({
    code: 200,
    data: [
      { orgCode: "ORG_CODE1", orgName: "财付通" },
      { orgCode: "ORG_CODE2", orgName: "支付宝" },
      { orgCode: "ORG_CODE3", orgName: "平安付" },
      { orgCode: "ORG_CODE4", orgName: "拉卡拉" },
      { orgCode: "ORG_CODE5", orgName: "银商务" },
      { orgCode: "ORG_CODE6", orgName: "货拉拉" },
      { orgCode: "ORG_CODE7", orgName: "运维平台1" },
      { orgCode: "ORG_CODE8", orgName: "运维平台2" },
    ],
  });
}
