import bisect
import datetime
import json
import os
import re
import time
from collections import defaultdict
from collections import OrderedDict
from pathlib import Path

from py.xml import html
from py.xml import raw

from . import __pypi_url__
from . import __version__
from .outcome import Outcome
from .result import TestResult
from .util import ansi_support


class HTMLReport:
    def __init__(self, logfile, config):
        logfile = Path(os.path.expandvars(logfile)).expanduser()
        self.logfile = logfile.absolute()
        self.test_logs = []
        self.title = self.logfile.name
        self.results = []
        self.errors = self.failed = 0
        self.passed = self.skipped = 0
        self.xfailed = self.xpassed = 0
        has_rerun = config.pluginmanager.hasplugin("rerunfailures")
        self.rerun = 0 if has_rerun else None
        self.self_contained = config.getoption("self_contained_html")
        self.config = config
        self.reports = defaultdict(list)

    def _appendrow(self, outcome, report):
        result = TestResult(outcome, report, self.logfile, self.config)
        if result.row_table is not None:
            index = bisect.bisect_right(self.results, result)
            self.results.insert(index, result)
            tbody = html.tbody(
                result.row_table,
                class_="{} results-table-row".format(result.outcome.lower()),
            )
            if result.row_extra is not None:
                tbody.append(result.row_extra)
            self.test_logs.insert(index, tbody)

    def append_passed(self, report):
        if report.when == "call":
            if hasattr(report, "wasxfail"):
                self.xpassed += 1
                self._appendrow("XPassed", report)
            else:
                self.passed += 1
                self._appendrow("Passed", report)

    def append_failed(self, report):
        if getattr(report, "when", None) == "call":
            if hasattr(report, "wasxfail"):
                # pytest < 3.0 marked xpasses as failures
                self.xpassed += 1
                self._appendrow("XPassed", report)
            else:
                self.failed += 1
                self._appendrow("Failed", report)
        else:
            self.errors += 1
            self._appendrow("Error", report)

    def append_rerun(self, report):
        self.rerun += 1
        self._appendrow("Rerun", report)

    def append_skipped(self, report):
        if hasattr(report, "wasxfail"):
            self.xfailed += 1
            self._appendrow("XFailed", report)
        else:
            self.skipped += 1
            self._appendrow("Skipped", report)

    def _generate_report(self, session):
        suite_stop_time = time.time()
        suite_time_delta = suite_stop_time - self.suite_start_time
        numtests = self.passed + self.failed + self.xpassed + self.xfailed
        generated = datetime.datetime.now()

        css_path = Path(__file__).parent / "resources" / "style.css"
        self.style_css = css_path.read_text()

        if ansi_support():
            ansi_css = [
                "\n/******************************",
                " * ANSI2HTML STYLES",
                " ******************************/\n",
            ]
            ansi_css.extend([str(r) for r in ansi_support().style.get_styles()])
            self.style_css += "\n".join(ansi_css)

        # <DF> Add user-provided CSS
        for path in self.config.getoption("css"):
            self.style_css += "\n/******************************"
            self.style_css += "\n * CUSTOM CSS"
            self.style_css += f"\n * {path}"
            self.style_css += "\n ******************************/\n\n"
            self.style_css += Path(path).read_text()

        css_href = "assets/style.css"
        html_css = html.link(href=css_href, rel="stylesheet", type="text/css")
        if self.self_contained:
            html_css = html.style(raw(self.style_css))

        session.config.hook.pytest_html_report_title(report=self)

        head = html.head(html.meta(charset="utf-8"), html.title(self.title), html_css)

        outcomes = [
            Outcome("passed", self.passed, label="通过"),
            Outcome("skipped", self.skipped, label="跳过"),
            Outcome("failed", self.failed, label="失败"),
            #Outcome("error", self.errors, label="错误"),
            #Outcome("xfailed", self.xfailed, label="预期失败"),
            #Outcome("xpassed", self.xpassed, label="预期通过"),
        ]

        if self.rerun is not None:
            outcomes.append(Outcome("重跑", self.rerun))

        summary = [
            html.p(f"运行了 {numtests} 个检查，历时: {suite_time_delta:.2f} 秒. "),
            html.p(
                "(取消)勾选复选框, 以便筛选测试结果",
                class_="filter",
                hidden="true",
            ),
        ]

        for i, outcome in enumerate(outcomes, start=1):
            summary.append(outcome.checkbox)
            summary.append(outcome.summary_item)
            if i < len(outcomes):
                summary.append(", ")

        cells = [
            html.th("通过/失败", class_="sortable result initial-sort", col="result"),
            html.th("检查", class_="sortable", col="name"),
            html.th("耗时", class_="sortable", col="duration"),
            html.th("Links", class_="sortable links", col="links"),
        ]
        session.config.hook.pytest_html_results_table_header(cells=cells)

        results = [
            html.h2("结果"),
            html.table(
                [
                    html.thead(
                        html.tr(cells),
                        html.tr(
                            [
                                html.th(
                                    "无结果",
                                    colspan=len(cells),
                                )
                            ],
                            id="not-found-message",
                            hidden="true",
                        ),
                        id="results-table-head",
                    ),
                    self.test_logs,
                ],
                id="results-table",
            ),
        ]

        main_js_path = Path(__file__).parent / "resources" / "main.js"
        main_js = main_js_path.read_text()

        body = html.body(
            html.script(raw(main_js)),
            html.h1(self.title, align = 'center'),
            html.p(
                "报告生成时间: {} 工具地址: ".format(
                    generated.strftime("%Y-%m-%d %H:%M:%S")
                ),
                html.a("pytest-html", href=__pypi_url__),
                f" v{__version__}",
                align = 'center'
            ),
            onLoad="init()",
        )

        body.extend(self._generate_environment(session.config))

        summary_prefix, summary_postfix = [], []
        session.config.hook.pytest_html_results_summary(
            prefix=summary_prefix, summary=summary, postfix=summary_postfix
        )
        body.extend([html.h2("概要")] + summary_prefix + summary + summary_postfix)

        body.extend(results)

        doc = html.html(head, body)

        unicode_doc = "<!DOCTYPE html>\n{}".format(doc.unicode(indent=2))

        # Fix encoding issues, e.g. with surrogates
        unicode_doc = unicode_doc.encode("utf-8", errors="xmlcharrefreplace")
        return unicode_doc.decode("utf-8")

    def _generate_environment(self, config):
        if not hasattr(config, "_metadata") or config._metadata is None:
            return []

        metadata = config._metadata
        environment = [html.h2("环境")]
        rows = []

        keys = [k for k in metadata.keys()]
        if not isinstance(metadata, OrderedDict):
            keys.sort()

        for key in keys:
            value = metadata[key]
            if self._is_redactable_environment_variable(key, config):
                black_box_ascii_value = 0x2593
                value = "".join(chr(black_box_ascii_value) for char in str(value))

            if isinstance(value, str) and value.startswith("http"):
                value = html.a(value, href=value, target="_blank")
            elif isinstance(value, (list, tuple, set)):
                value = ", ".join(str(i) for i in sorted(map(str, value)))
            elif isinstance(value, dict):
                sorted_dict = {k: value[k] for k in sorted(value)}
                value = json.dumps(sorted_dict)
            raw_value_string = raw(str(value))
            rows.append(html.tr(html.td(key), html.td(raw_value_string)))

        environment.append(html.table(rows, id="environment"))
        return environment

    def _is_redactable_environment_variable(self, environment_variable, config):
        redactable_regexes = config.getini("environment_table_redact_list")
        for redactable_regex in redactable_regexes:
            if re.match(redactable_regex, environment_variable):
                return True

        return False

    def _save_report(self, report_content):
        dir_name = self.logfile.parent
        assets_dir = dir_name / "assets"

        dir_name.mkdir(parents=True, exist_ok=True)
        if not self.self_contained:
            assets_dir.mkdir(parents=True, exist_ok=True)

        self.logfile.write_text(report_content)
        if not self.self_contained:
            style_path = assets_dir / "style.css"
            style_path.write_text(self.style_css)

    def _post_process_reports(self):
        for test_name, test_reports in self.reports.items():
            report_outcome = "passed"
            wasxfail = False
            failure_when = None
            full_text = ""
            extras = []
            duration = 0.0

            # in theory the last one should have all logs so we just go
            #  through them all to figure out the outcome, xfail, duration,
            #    extras, and when it swapped from pass
            for test_report in test_reports:
                if test_report.outcome == "rerun":
                    # reruns are separate test runs for all intensive purposes
                    self.append_rerun(test_report)
                else:
                    full_text += test_report.longreprtext
                    extras.extend(getattr(test_report, "extra", []))
                    duration += getattr(test_report, "duration", 0.0)

                    if (
                        test_report.outcome not in ("passed", "rerun")
                        and report_outcome == "passed"
                    ):
                        report_outcome = test_report.outcome
                        failure_when = test_report.when

                    if hasattr(test_report, "wasxfail"):
                        wasxfail = True

            # the following test_report.<X> = settings come at the end of us
            #  looping through all test_reports that make up a single
            #    case.

            # outcome on the right comes from the outcome of the various
            #  test_reports that make up this test case
            #    we are just carrying it over to the final report.
            test_report.outcome = report_outcome
            test_report.when = "call"
            test_report.nodeid = test_name
            test_report.longrepr = full_text
            test_report.extra = extras
            test_report.duration = duration

            if wasxfail:
                test_report.wasxfail = True

            if test_report.outcome == "passed":
                self.append_passed(test_report)
            elif test_report.outcome == "skipped":
                self.append_skipped(test_report)
            elif test_report.outcome == "failed":
                test_report.when = failure_when
                self.append_failed(test_report)

    def pytest_runtest_logreport(self, report):
        self.reports[report.nodeid].append(report)

    def pytest_collectreport(self, report):
        if report.failed:
            self.append_failed(report)

    def pytest_sessionstart(self, session):
        self.suite_start_time = time.time()

    def pytest_sessionfinish(self, session):
        self._post_process_reports()
        report_content = self._generate_report(session)
        self._save_report(report_content)

    def pytest_terminal_summary(self, terminalreporter):
        terminalreporter.write_sep("-", f"generated html file: {self.logfile.as_uri()}")
