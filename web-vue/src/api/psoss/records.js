// 获取用户所属机构列表
export function getUserOrgList() {
  return Promise.resolve({
    code: 200,
    data: [
      { orgCode: "ORG_CODE1", orgName: "运维平台" },
      { orgCode: "ORG_CODE2", orgName: "运维平台1" },
      { orgCode: "ORG_CODE3", orgName: "机构1" },
    ],
  });
}
