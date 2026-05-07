from __future__ import annotations

import unittest
from io import BytesIO
from zipfile import ZipFile

from src.resume_bot.xlsx_export import build_xlsx_workbook


class XlsxExportTests(unittest.TestCase):
    def test_build_xlsx_workbook_contains_expected_parts(self) -> None:
        payload = build_xlsx_workbook(
            [
                {
                    "name": "全部岗位",
                    "rows": [
                        ["推荐结果", "公司", "岗位"],
                        ["推荐", "快手", "内容运营"],
                    ],
                },
                {
                    "name": "未推荐",
                    "rows": [
                        ["推荐结果", "公司", "岗位"],
                        ["未推荐", "广发证券", "数据开发岗"],
                    ],
                },
            ]
        )
        with ZipFile(BytesIO(payload)) as archive:
            names = set(archive.namelist())
            self.assertIn("[Content_Types].xml", names)
            self.assertIn("xl/workbook.xml", names)
            self.assertIn("xl/worksheets/sheet1.xml", names)
            self.assertIn("xl/worksheets/sheet2.xml", names)
            workbook = archive.read("xl/workbook.xml").decode("utf-8")
            self.assertIn("全部岗位", workbook)
            self.assertIn("未推荐", workbook)


if __name__ == "__main__":
    unittest.main()
