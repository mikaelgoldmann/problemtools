#!/usr/bin/env python

import glob
import string
import hashlib
import collections
import os
import signal
import re
import shutil
import logging
import yaml
import tempfile
import sys
import copy
import random
from optparse import OptionParser
from program import Program
import problem2pdf
import problem2html


def get_programs(dir, pattern='.*', includedir=None, error_handler=logging):
    if not os.path.isdir(dir):
        return []
    ret = []
    for f in sorted(os.listdir(dir)):
        try:
            if re.match(pattern, f):
                path = os.path.join(dir, f)
                ret.append(Program(path, includedir=includedir))
            else:
                error_handler.info("Ignoring '%s'; invalid filename" % f)
        except Exception as e:
            error_handler.info(e)
    return ret


def locate_program(candidatePaths):
    for p in candidatePaths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return Program(p, True)
    return None


def locate_interactive():
    defaultPaths = [os.path.join(os.path.dirname(__file__),
                                 'interactive', 'interactive'),
                    '/usr/local/kattis/bin/interactive']
    return locate_program(defaultPaths)


def locate_default_validator():
    defaultPaths = [os.path.join(os.path.dirname(__file__),
                                 'default_validator', 'default_validator'),
                    '/usr/local/kattis/bin/default_validator']
    return locate_program(defaultPaths)


def locate_default_grader():
    defaultPaths = [os.path.join(os.path.dirname(__file__),
                                 'default_grader', 'default_grader'),
                    '/usr/local/kattis/bin/default_grader']
    return locate_program(defaultPaths)


def is_TLE(status, may_signal_with_usr1=False):
    return (os.WIFSIGNALED(status) and
            (os.WTERMSIG(status) == signal.SIGXCPU or
             (may_signal_with_usr1 and os.WTERMSIG(status) == signal.SIGUSR1)))


def is_RTE(status):
    return not os.WIFEXITED(status) or os.WEXITSTATUS(status)


class SubmissionResult:
    def __init__(self, verdict, score=None, subresults=None, reason=None):
        self.verdict = verdict
        self.score = score
        self.subresults = subresults
        self.reason = reason
        self.runtime = -1.0
        self.runtime_reason = None
        self.ac_runtime = -1.0
        self.ac_runtime_reason = None
        if subresults is not None:
            for r in subresults:
                if r.runtime > self.runtime:
                    self.runtime = r.runtime
                    self.runtime_reason = r.runtime_reason
                if r.ac_runtime > self.ac_runtime:
                    self.ac_runtime = r.ac_runtime
                    self.ac_runtime_reason = r.ac_runtime_reason

    def __str__(self):
        res = self.verdict
        score_str = ''
        reason_str = ''
        if self.score is not None and self.verdict == 'AC':
            score_str = ' (%.0f)' % self.score
        if self.verdict != 'AC' and self.reason is not None:
            reason_str = 'dataset: %s, ' % self.reason
        return '%s%s [%sCPU: %.2fs @ %s]' % (self.verdict, score_str,
                                             reason_str,
                                             self.runtime, self.runtime_reason)


class ProblemAspect:
    errors = 0
    warnings = 0
    _check_res = None

    def error(self, msg):
        self._check_res = False
        ProblemAspect.errors += 1
        logging.error('in %s: %s' % (self, msg))

    def warning(self, msg):
        ProblemAspect.warnings += 1
        logging.warning('in %s: %s' % (self, msg))

    def msg(self, msg):
        print msg

    def info(self, msg):
        logging.info(': %s' % (msg))

    def debug(self, msg):
        logging.debug(': %s' % (msg))


class TestCase(ProblemAspect):
    def __init__(self, problem, base, testcasegroup):
        self._base = base
        self.infile = base + '.in'
        self.ansfile = base + '.ans'
        self._problem = problem
        self.testcasegroup = testcasegroup

    def check_newlines(self, file):
        data = open(file).read()
        if data.find('\r') != -1:
            self.warning('The file %s contains non-standard line breaks.'
                         % file)

    def strip_path_prefix(self, path):
        return '/'.join(path.split('/')[4:])

    def check(self):
        if self._check_res is not None:
            return self._check_res
        self._check_res = True
        self.check_newlines(self.infile)
        self.check_newlines(self.ansfile)
        self._problem.input_format_validators.validate(self)
        anssize = os.path.getsize(self.ansfile)
        outputlim = self._problem.config.get('limits')['output']
        if anssize > outputlim * 1024 * 1024:
            self.error('Answer file (%.1f Mb) larger than output limit (%d Mb), you need to increase output limit' % (anssize / 1024.0 / 1024.0, outputlim))
        elif 2 * anssize > outputlim * 1024 * 1024:
            self.warning('Answer file (%.1f Mb) is within 50%% of output limit (%d Mb), you might want to increase output limit' % (anssize / 1024.0 / 1024.0, outputlim))
#        self._problem.output_validators.validate(self, self.ansfile, self)
        return self._check_res

    def __str__(self):
        return 'test case %s' % self.strip_path_prefix(self._base)

    def run_submission(self, sub, timelim_low=1000, timelim_high=1000):
        outfile = os.path.join(self._problem.probdir, 'output')
        if sys.stdout.isatty():
            msg = 'Running %s on %s...' % (sub.name, self)
            sys.stdout.write('%s' % msg)
            sys.stdout.flush()
        if 'interactive' in self._problem.config.get('validation-params'):
            res2 = self._problem.output_validators.validate_interactive(self, sub, timelim_high, self._problem.submissions)
        else:
            status, runtime = sub.run(self.infile, outfile, timelim=timelim_high, logger=self)
            if is_TLE(status):
                res2 = SubmissionResult('TLE', score=self._problem.config.get('grading')['reject_score'])
            elif is_RTE(status):
                res2 = SubmissionResult('RTE', score=self._problem.config.get('grading')['reject_score'])
            else:
                res2 = self._problem.output_validators.validate(self, outfile, self._problem.submissions)
            res2.runtime = runtime
        if sys.stdout.isatty():
            sys.stdout.write('%s' % '\b' * (len(msg)))
        if res2.runtime <= timelim_low:
            res1 = res2
        else:
            res1 = SubmissionResult('TLE', score=self._problem.config.get('grading')['reject_score'])
        res1.reason = res2.reason = self
        res1.runtime_reason = res2.runtime_reason = self
        res1.runtime = res2.runtime
        if res1.verdict == 'AC':
            res1.ac_runtime = res1.runtime
            res1.ac_runtime_reason = res1.runtime_reason
        if res2.verdict == 'AC':
            res2.ac_runtime = res2.runtime
            res2.ac_runtime_reason = res2.runtime_reason
        self.info('Test file result: %s)' % (res1))
        return (res1, res2)

    def all_datasets(self):
        return [self._base]


class TestCaseGroup(ProblemAspect):
    _DEFAULT_CONFIG = {'grading': 'default',
                       'grader_flags': '',
                       'input_validator_flags': '',
                       'output_validator_flags': ''}

    def __init__(self, problem, datadir, parent=None):
        self._parent = parent
        self._problem = problem
        self._datadir = datadir
        self.debug('  Loading test data group %s' % datadir)
        configfile = os.path.join(self._datadir, 'testdata.yaml')
        if os.path.isfile(configfile):
            try:
                self.config = yaml.safe_load(file(configfile))
            except Exception as e:
                self.error(e)
                self.config = {}
        elif parent is not None:
            self.config = parent.config.copy()
        else:
            self.config = {}

        for field, default in TestCaseGroup._DEFAULT_CONFIG.iteritems():
            if not field in self.config:
                self.config[field] = default

        seenfiles = set()
        self._items = []
        for f in sorted(os.listdir(datadir)):
            f = os.path.join(datadir, f)
            if os.path.isdir(f):
                self._items.append(TestCaseGroup(problem, f, self))
            else:
                base, ext = os.path.splitext(f)
                if ext == '.ans' and os.path.isfile(base + '.in'):
                    self._items.append(TestCase(problem, base, self))

    def __str__(self):
        return 'test case group %s' % '/'.join(self._datadir.split('/')[3:])

    def get_subgroup(self, name):
        return next([i for i in self._items if isinstance(i, TestCaseGroup) and os.path.basename(i._basedir) == name], None)

    def check(self):
        if self._check_res is not None:
            return self._check_res
        self._check_res = True

        if self.config['grading'] not in ['default', 'custom']:
            self.error("Invalid grading policy in testdata.yaml")

        if self.config['grading'] == 'custom' and len(self._problem.graders._graders) == 0:
            self._problem.graders.error('%s has custom grading but no custom graders provided' % self)
        if self.config['grading'] == 'default' and Graders._default_grader is None:
            self._problem.graders.error('%s has default grading but I could not find default grader' % self)

        for field in self.config.keys():
            if field not in TestCaseGroup._DEFAULT_CONFIG.keys():
                self.warning("Unknown key '%s' in '%s'" % (field, os.path.join(self._datadir, 'testdata.yaml')))

        infiles = glob.glob(os.path.join(self._datadir, '*.in'))
        ansfiles = glob.glob(os.path.join(self._datadir, '*.ans'))

        if self._parent is None:
            seen_secret = False
            seen_sample = False
            for item in self._items:
                if not isinstance(item, TestCaseGroup):
                    self.error("Can't have individual test data files at top level")
                else:
                    name = os.path.basename(item._datadir)
                    if name == 'secret':
                        seen_secret = True
                    elif name == 'sample':
                        seen_sample = True
                    else:
                        self.error("Test data at top level can only have the groups sample and secret")
                        self.debug(self._items)
            if not seen_secret:
                self.error("No secret data provided")
            if not seen_sample:
                self.warning("No sample data provided")
            hashes = collections.defaultdict(list)
            for root, dirs, files in os.walk(self._datadir):
                for filename in files:
                    if filename[-3:] == ".in":
                        md5 = hashlib.md5()
                        with open(os.path.join(root, filename), 'rb') as f:
                            for buf in iter(lambda: f.read(1024),  b''):
                                md5.update(buf)
                        filehash = md5.digest()
                        filepath = os.path.join(root, filename)
                        hashes[filehash].append('/'.join(filepath.split('/')[4:]))
            for _, files in hashes.iteritems():
                if len(files) > 1:
                    self.warning("Identical input files: '%s'" % str(files))

        for f in infiles:
            if not f[:-3] + '.ans' in ansfiles:
                self.error("No matching answer file for input '%s'" % f)
        for f in ansfiles:
            if not f[:-4] + '.in' in infiles:
                self.error("No matching input file for answer '%s'" % f)

        for subdata in self._items:
            subdata.check()

        return self._check_res

    def compute_result(self, sub_results, probtype, on_reject, shadow_result=False):
        verdict_value = {'JE': -1, 'CE': 0, 'TLE': 1, 'RTE': 2, 'WA': 3, 'AC': 4}
        verdict = 'AC'
        reason = None
        if on_reject == 'first_error':
            first_fail = next((r for r in sub_results if r.verdict != 'AC'), None)
            if first_fail is not None:
                verdict = first_fail.verdict
                reason = first_fail.reason
        elif on_reject == 'worst_error':
            worst_fail = min(sub_results, key=lambda r: verdict_value[r.verdict])
            if worst_fail is not None:
                verdict = worst_fail.verdict
                reason = worst_fail.reason
        if probtype == 'scoring' and verdict == 'AC':
            return self._problem.graders.grade(self, sub_results, shadow_result)
        return SubmissionResult(verdict, subresults=sub_results, reason=reason)

    def run_submission(self, sub, timelim_low, timelim_high):
        self.info('Running on %s' % self)
        subres1 = []
        subres2 = []
        probtype = self._problem.config.get('type')
        on_reject = self._problem.config.get('grading')['on_reject']
        for subdata in self._items:
            (r1, r2) = subdata.run_submission(sub, timelim_low, timelim_high)
            subres1.append(r1)
            subres2.append(r2)
            if on_reject == 'first_error' and r2.verdict != 'AC':
                break
        return (self.compute_result(subres1, probtype, on_reject),
                self.compute_result(subres2, probtype, on_reject, shadow_result=True))

    def all_datasets(self):
        res = []
        for subdata in self._items:
            res += subdata.all_datasets()
        return res


class ProblemConfig(ProblemAspect):
    _MANDATORY_CONFIG = ['name']
    _OPTIONAL_CONFIG = {
        'uuid': '',
        'type': 'pass-fail',
        'author': '',
        'source': '',
        'source_url': '',
        'license': 'unknown',
        'rights_owner': '',
        'keywords': '',
        'limits': {'time_multiplier': 5,
                   'time_safety_margin': 2,
                   'memory': 1024,
                   'output': 8,
                   'compilation_time': 60,
                   'validation_time': 60,
                   'validation_memory': 1024,
                   'validation_output': 8},
        'validation': 'default',
        'validator_flags': '',
        'grading': {'on_reject': 'first_error',
                    'accept_score': 1.0,
                    'reject_score': 0.0,
                    'objective': 'max',
                    'range': '-inf +inf'},
        'libraries': '',
        'languages': ''
        }
    _VALID_LICENSES = ['unknown', 'public domain', 'cc0', 'cc by', 'cc by-sa', 'educational', 'permission']

    def __init__(self, problem):
        self.debug('  Loading problem config')
        self._problem = problem
        self.configfile = os.path.join(problem.probdir, 'problem.yaml')
        self._data = {}

        if os.path.isfile(self.configfile):
            try:
                self._data = yaml.safe_load(file(self.configfile))
                # Loading empty yaml yields None, for no apparent reason...
                if self._data is None:
                    self._data = {}
            except Exception as e:
                self.error(e)

        # Add config items from problem statement e.g. name
        self._data.update(problem.statement.get_config())

        # Populate rights_owner unless license is public domain
        if 'rights_owner' not in self._data and ('license' not in self._data or self._data['license'] != 'public_domain'):
            if 'author' in self._data:
                self._data['rights_owner'] = self._data['author']
            elif 'source' in self._data:
                self._data['rights_owner'] = self._data['source']

        if 'license' in self._data:
            self._data['license'] = self._data['license'].lower()

        # Ugly backwards compatibility hack
        if 'name' in self._data and not type(self._data['name']) is dict:
            self._data['name'] = {'': self._data['name']}

        for field, default in copy.deepcopy(ProblemConfig._OPTIONAL_CONFIG).iteritems():
            if not field in self._data:
                self._data[field] = default
            elif type(default) is dict:
                self._data[field] = dict(default.items() + self._data[field].items())

        self._origdata = copy.deepcopy(self._data)

        val = self._data['validation'].split()
        self._data['validation-type'] = val[0]
        self._data['validation-params'] = val[1:]

        if self._data['type'] == 'pass-fail':
            self._data['grading']['accept_score'] = None
            self._data['grading']['reject_score'] = None

        self._data['grading']['custom_scoring'] = False
        for param in self._data['validation-params']:
            if param == 'score':
                self._data['grading']['custom_scoring'] = True
            elif param == 'interactive':
                pass

    def __str__(self):
        return 'problem configuration'

    def get(self, key=None):
        if key:
            return self._data[key]
        return self._data

    def check(self):
        if self._check_res is not None:
            return self._check_res
        self._check_res = True

        if not os.path.isfile(self.configfile):
            self.error("No config file %s found" % self.configfile)

        for field in ProblemConfig._MANDATORY_CONFIG:
            if not field in self._data:
                self.error("Mandatory field '%s' not provided" % field)

        for field, value in self._origdata.iteritems():
            if value is None:
                self.error("Field '%s' provided in problem.yaml but is empty" % field)
            if field not in ProblemConfig._OPTIONAL_CONFIG.keys() and field not in ProblemConfig._MANDATORY_CONFIG:
                self.warning("Unknown field '%s' provided in problem.yaml" % field)

        # Check type
        if not self._data['type'] in ['pass-fail', 'scoring']:
            self.error("Invalid value '%s' for type" % self._data['type'])

        # Check rights_owner
        if self._data['license'] == 'public domain':
            if self._data['rights_owner'].strip() != '':
                self.error('Can not have a rights_owner for a problem in public domain')
        elif self._data['license'] != 'unknown':
            if self._data['rights_owner'].strip() == '':
                self.error('No author, source or rights_owner provided')

        # Check source_url
        if (self._data['source_url'].strip() != '' and
            self._data['source'].strip() == ''):
            self.error('Can not provide source_url without also providing source')

        # Check license
        if not self._data['license'] in ProblemConfig._VALID_LICENSES:
            self.error("Invalid value for license: %s.\n  Valid licenses are %s" % (self._data['license'], ProblemConfig._VALID_LICENSES))
        elif self._data['license'] == 'unknown':
            self.warning("License is 'unknown'")

        if not self._data['grading']['on_reject'] in ['first_error', 'worst_error', 'grade']:
            self.error("Invalid value '%s' for on_reject policy" % self._data['grading']['on_reject'])

        if self._data['type'] == 'pass-fail' and self._data['grading']['on_reject'] == 'grade':
            self.error("Invalid on_reject policy '%s' for problem type '%s'" % (self._data['grading']['on_reject'], self._data['type']))

        if not self._data['validation-type'] in ['default', 'custom']:
            self.error("Invalid value '%s' for validation, first word must be 'default' or 'custom'" % self._data['validation'])

        if self._data['validation-type'] == 'default' and len(self._data['validation-params']) > 0:
            self.error("Invalid value '%s' for validation" % (self._data['validation']))

        if self._data['validation-type'] == 'custom':
            for param in self._data['validation-params']:
                if param not in['score', 'interactive']:
                    self.error("Invalid parameter '%s' for custom validation" % param)

        # Some things not yet implemented
        if self._data['grading']['on_reject'] == 'worst_error':
            self.error("'on_reject: worst_error' not yet supported")
        if self._data['libraries'] != '':
            self.error("Libraries not yet supported")
        if self._data['languages'] != '':
            self.error("Languages not yet supported")

        return self._check_res


class ProblemStatement(ProblemAspect):
    def __init__(self, problem):
        self.debug('  Loading problem statement')
        self._problem = problem
        self.languages = []
        glob_path = os.path.join(problem.probdir, 'problem_statement', 'problem.')
        if glob.glob(glob_path + 'tex'):
            self.languages.append('')
        for f in glob.glob(glob_path + '[a-z][a-z].tex'):
            self.languages.append(re.search("problem.([a-z][a-z]).tex$", f).group(1))

    def check(self):
        if self._check_res is not None:
            return self._check_res
        self._check_res = True

        if not self.languages:
            self.error('No problem statements found (expected problem.tex or problem.[a-z][a-z].tex in problem_statement directory)')
        if '' in self.languages and 'en' in self.languages:
            self.error("Can't supply both problem.tex and problem.en.tex")
        pdfopt = problem2pdf.ConvertOptions()
        pdfopt.nopdf = True
        pdfopt.quiet = True
        htmlopt = problem2html.ConvertOptions()
        htmlopt.destdir = os.path.join(self._problem._basedir, '__html')
        htmlopt.quiet = True

        for lang in self.languages:
            pdfopt.language = lang
            htmlopt.language = lang
            pdf_ok = True
            try:
                if not problem2pdf.convert(self._problem.probdir, pdfopt):
                    langparam = ''
                    if lang != '':
                        langparam = '-l ' + lang
                    self.error('Could not compile problem statement for language "%s".  Run problem2pdf %s on the problem to diagnose.' % (lang, langparam))
            except Exception as e:
                self.error('Error raised when checking problem statement for language %s:\n%s' % (lang, e))
            if not pdf_ok:
                continue
            try:
                problem2html.convert(self._problem.probdir, htmlopt)
            except Exception as e:
                langparam = ''
                if lang != '':
                    langparam = '-l ' + lang
                self.error('Could not convert problem statement to html for language "%s".  Run problem2html %s on the problem to diagnose.' % (lang, langparam))
        return self._check_res

    def __str__(self):
        return 'problem statement'

    def get_config(self):
        ret = {}
        for lang in self.languages:
            if lang != '':
                lang = lang + '.'
            stmt = open(os.path.join(self._problem.probdir, 'problem_statement', 'problem.' + lang + 'tex')).read()
            patterns = [('\\problemname{(.*)}', 'name'),
                        ('^%%\s*plainproblemname:(.*)$', 'name')
                        ]
            for tup in patterns:
                pattern = tup[0]
                dest = tup[1]
                hit = re.search(pattern, stmt, re.MULTILINE)
                if hit:
                    if not dest in ret:
                        ret[dest] = {}
                    ret[dest][lang] = hit.group(1).strip()
        return ret


def generate_random_input():
    return ''.join(random.choice(string.printable) for _ in range(200))


class InputFormatValidators(ProblemAspect):

    def __init__(self, problem):
        self._problem = problem
        self._validators = get_programs(os.path.join(problem.probdir, 'input_format_validators'), error_handler=self)
        self._seen_flags = []
        fd, self._random_input = tempfile.mkstemp()
        os.close(fd)
        f = open(self._random_input, "wb")
        f.write(generate_random_input())
        f.close()

    def __str__(self):
        return 'input format validators'

    def check(self):
        if self._check_res is not None:
            return self._check_res
        self._check_res = True
        if len(self._validators) == 0:
            self.error('No input format validators found')

        for val in self._validators:
            if not val.compile():
                self.error('Compile error for input format validator %s' % val.name)

        return self._check_res

    def validate(self, testcase):
        flags = testcase.testcasegroup.config['input_validator_flags'].split()
        should_test = flags not in self._seen_flags
        if should_test:
            self._seen_flags.append(flags)
        for val in self._validators:
            if val.compile():
                if should_test:
                    self._seen_flags.append(flags)
                    status, runtime = val.run(self._random_input, args=flags, logger=self)
                    if os.WEXITSTATUS(status) == 42:
                        testcase.testcasegroup.warning("The validator flags of %s and validator %s does not reject random input" % (testcase.testcasegroup, val))
                status, runtime = val.run(testcase.infile, args=flags, logger=self)
                if not os.WIFEXITED(status):
                    testcase.error('Input format validator %s crashed on input %s' % (val, testcase.infile))
                if os.WEXITSTATUS(status) != 42:
                    testcase.error('Input format validator %s did not accept input %s, exit code: %d' % (val, testcase.infile, os.WEXITSTATUS(status)))


class Graders(ProblemAspect):
    _default_grader = locate_default_grader()

    def __init__(self, problem):
        self._problem = problem
        self._graders = get_programs(os.path.join(problem.probdir, 'graders'), error_handler=self)

    def __str__(self):
        return 'graders'

    def check(self):
        if self._check_res is not None:
            return self._check_res
        self._check_res = True

        if self._problem.config.get('type') == 'pass-fail' and len(self._graders) > 0:
            self.error('There are grader programs but the problem is pass-fail')

        for grader in self._graders:
            if not grader.compile():
                self.error('Compile error for grader %s' % grader.name)
        return self._check_res

    def grade(self, testcasegroup, results, shadow_result=False):
        if testcasegroup.config['grading'] == 'default':
            graders = [self._default_grader]
        else:
            graders = self._graders
        grader_input = ''.join(['%s %s\n' % (r.verdict, r.score) for r in results])
        grader_output_re = '^((AC)|(WA)|(TLE)|(RTE))\s+[0-9.]+\s*$'
        verdict = 'AC'
        score = 0

        self.debug('Grading %d results:\n%s' % (len(results), grader_input))
        self.debug('Grader flags: %s' % (testcasegroup.config.get('grader_flags')))

        for grader in graders:
            if grader is not None and grader.compile():
                fd, infile = tempfile.mkstemp()
                os.close(fd)
                fd, outfile = tempfile.mkstemp()
                os.close(fd)

                open(infile, 'w').write(grader_input)

                status, runtime = grader.run(infile, outfile,
                                             args=testcasegroup.config.get('grader_flags').split(),
                                             logger=self)

                grader_output = open(outfile, 'r').read()
                os.remove(infile)
                os.remove(outfile)
                if not os.WIFEXITED(status):
                    self.error('Judge error: grader %s crashed' % (grader.name))
                    self.debug('Grader input:\n%s' % grader_input)
                    return SubmissionResult('JE', score=0.0, subresults=results)
#                ret = os.WEXITSTATUS(status)
#                if ret != 42:
#                    self.error('Judge error: exit code %d for grader %s' % (ret, grader.name))
#                    self.debug('Grader input: %s\n' % grader_input)
#                    return SubmissionResult('JE', 0.0, results)

                if not re.match(grader_output_re, grader_output):
                    self.error('Judge error: invalid format of grader output')
                    self.debug('Output must match: "%s"' % grader_output_re)
                    self.debug('Output was: "%s"' % grader_output)
                    return SubmissionResult('JE', score=0.0, subresults=results)

                verdict, score = grader_output.split()
                score = float(score)
        # TODO: check that all graders give same result

        if not shadow_result:
            self.info('Grade on %s is %s (%s)' % (testcasegroup, verdict, score))
        return SubmissionResult(verdict, score=score, subresults=results)


class OutputValidators(ProblemAspect):
    _default_validator = locate_default_validator()

    def __init__(self, problem):
        self._problem = problem
        self._validators = get_programs(os.path.join(problem.probdir, 'output_validators'), error_handler=self)

    def __str__(self):
        return 'output validators'

    def check(self):
        if self._check_res is not None:
            return self._check_res
        self._check_res = True

        if self._problem.config.get('validation') == 'default' and self._validators:
            self.error('There are validator programs but problem.yaml has validation = "default"')
        elif self._problem.config.get('validation') != 'default' and not self._validators:
            self.error('problem.yaml specifies custom validator but no validator programs found')

        if self._problem.config.get('validation') == 'default' and self._default_validator is None:
            self.error('Unable to locate default validator')

        for val in self._validators:
            if not val.compile():
                self.error('Compile error for output validator %s' % val.name)
        return self._check_res

    def _parse_validator_results(self, val, status, feedbackdir, errorhandler):
        custom_score = self._problem.config.get('grading')['custom_scoring']
        score = None
        # TODO: would be good to have some way of displaying the feedback for debugging uses
        score_file = os.path.join(feedbackdir, 'score.txt')
        if not custom_score and os.path.isfile(score_file):
            errorhandler.error('validator produced "score.txt" but problem does not have custom scoring activated')
        if custom_score:
            if os.path.isfile(score_file):
                try:
                    score_str = open(score_file).read()
                    score = float(score_str)
                except Exception as e:
                    errorhandler.error('failed to check validator score: %s' % e)
            else:
                errorhandler.error('problem has custom scoring but validator did not produce "score.txt"')

        if not os.WIFEXITED(status):
            errorhandler.error('Judge error: output validator %s crashed, status %d' % (val.name, status))
            return SubmissionResult('JE')
        ret = os.WEXITSTATUS(status)
        if ret not in [42, 43]:
            errorhandler.error('Judge error: exit code %d for output validator %s' % (ret, val.name))
            return SubmissionResult('JE')

        if ret == 43:
            if score is None:
                score = self._problem.config.get('grading')['reject_score']
            return SubmissionResult('WA', score=score)
        if score is None:
            score = self._problem.config.get('grading')['accept_score']
        return SubmissionResult('AC', score=score)

    def _actual_validators(self):
        vals = self._validators
        if self._problem.config.get('validation') == 'default':
            vals = [self._default_validator]
        return vals

    def validate_interactive(self, testcase, submission, timelim, errorhandler):
        interactive_output_re = '\d+ \d+\.\d+ \d+ \d+\.\d+'
        res = SubmissionResult('JE')
        interactive = locate_interactive()
        if interactive is None:
            errorhandler.error('Could not locate interactive runner')
            return res
        # file descriptor, wall time lim
        initargs = ['1', str(2 * timelim)]
        validator_args = [testcase.infile, testcase.ansfile, '<feedbackdir>']
        submission_args = submission.get_runcmd()
        for val in self._actual_validators():
            if val is not None and val.compile():
                feedbackdir = tempfile.mkdtemp(prefix='testprob', dir=self._problem.probdir)
                validator_args[2] = feedbackdir
                f = tempfile.NamedTemporaryFile(delete=False)
                interactive_out = f.name
                f.close()
                i_status, i_runtime = interactive.run(outfile=interactive_out,
                                                      args=initargs + val.get_runcmd() + validator_args + [';'] + submission_args)
                if is_RTE(i_status):
                    errorhandler.error('Interactive crashed, status %d' % i_status)
                else:
                    interactive_output = open(interactive_out).read()
                    errorhandler.debug('Interactive output: "%s"' % interactive_output)
                    if not re.match(interactive_output_re, interactive_output):
                        errorhandler.error('Output from interactive does not follow expected format, got output "%s"' % interactive_output)
                    else:
                        val_status, val_runtime, sub_status, sub_runtime = interactive_output.split()
                        sub_status = int(sub_status)
                        sub_runtime = float(sub_runtime)
                        val_status = int(val_status)
                        val_runtime = float(val_runtime)

                        if is_TLE(sub_status, True):
                            res = SubmissionResult('TLE', score=self._problem.config.get('grading')['reject_score'])
                        elif is_RTE(sub_status):
                            res = SubmissionResult('RTE', score=self._problem.config.get('grading')['reject_score'])
                        else:
                            res = self._parse_validator_results(val, val_status, feedbackdir, errorhandler)
                        res.runtime = sub_runtime

                os.unlink(interactive_out)
                shutil.rmtree(feedbackdir)
                if res.verdict != 'AC':
                    return res
        # TODO: check that all output validators give same result
        return res

    def validate(self, testcase, submission_output, errorhandler):
        for val in self._actual_validators():
            if val is not None and val.compile():
                feedbackdir = tempfile.mkdtemp(prefix='testprob', dir=self._problem.probdir)
                status, runtime = val.run(submission_output,
                                          args=[testcase.infile, testcase.ansfile, feedbackdir] + self._problem.config.get('validator_flags').split() + testcase.testcasegroup.config['output_validator_flags'].split(),
                                          logger=self)

                res = self._parse_validator_results(val, status, feedbackdir, errorhandler)
                shutil.rmtree(feedbackdir)
                if res.verdict != 'AC':
                    return res

        # TODO: check that all output validators give same result
        return res


class Submissions(ProblemAspect):
    _SUB_REGEXP = re.compile("^[a-zA-Z0-9][a-zA-Z0-9_.-]*[a-zA-Z0-9](\.c\+\+)?$")
    _VERDICTS = [
        ['AC', 'accepted', True],
        ['WA', 'wrong_answer', False],
        ['RTE', 'run_time_error', False],
        ['TLE', 'time_limit_exceeded', False],
        ]

    def __init__(self, problem):
        self._submissions = {}
        self._problem = problem
        srcdir = os.path.join(problem.probdir, 'submissions')
        for verdict in Submissions._VERDICTS:
            acr = verdict[0]
            self._submissions[acr] = get_programs(os.path.join(srcdir, verdict[1]),
                                                  Submissions._SUB_REGEXP,
                                                  os.path.join(problem.probdir, 'include'),
                                                  error_handler=self)

    def __str__(self):
        return 'submissions'

    def check_submission(self, sub, expected_verdict, timelim_low, timelim_high):
        (result1, result2) = self._problem.testdata.run_submission(sub, timelim_low, timelim_high)

        if result1.verdict != result2.verdict:
            self.warning('%s submission %s sensitive to time limit: limit of %s secs -> %s, limit of %s secs -> %s' % (expected_verdict, sub.name, timelim_low, result1.verdict, timelim_high, result2.verdict))

        if result1.verdict == expected_verdict:
            self.msg('   %s submission %s OK: %s' % (expected_verdict, sub.name, result1))
        elif result2.verdict == expected_verdict:
            self.msg('   %s submission %s OK with extra time: %s' % (expected_verdict, sub.name, result2))
        else:
            self.error('%s submission %s got %s' % (expected_verdict, sub.name, result1))
        return result1

    def check(self):
        if self._check_res is not None:
            return self._check_res
        self._check_res = True

        timelim_margin = 300  # 5 minutes
        timelim = 300
        if 'time_for_AC_submissions' in self._problem.config.get('limits'):
            timelim = timelim_margin = self._problem.config.get('limits')['time_for_AC_submissions']

        for verdict in Submissions._VERDICTS:
            acr = verdict[0]
            if verdict[2] and not self._submissions[acr]:
                self.error('Require at least one "%s" submission' % verdict[1])

            runtimes = []

            for sub in self._submissions[acr]:
                self.info('Check %s submission %s' % (acr, sub.name))

                if not sub.compile():
                    self.error('Compile error for %s submission %s' % (acr, sub.name))
                    continue

                res = self.check_submission(sub, acr, timelim, timelim_margin)
                runtimes.append(res.runtime)

            if acr == 'AC' and len(runtimes) > 0:
                max_runtime = max(runtimes)
                exact_timelim = max_runtime * self._problem.config.get('limits')['time_multiplier']
                timelim = max(1, int(0.5 + exact_timelim))
                self._problem.config.get('limits')['time'] = timelim
                timelim_margin = max(exact_timelim + 1,
                                     int(0.5 + exact_timelim * self._problem.config.get('limits')['time_safety_margin']))
                self.msg("   Slowest AC runtime: %.3lf, setting timelim to %d secs, safety margin to %d secs" % (max_runtime, timelim, timelim_margin))

        return self._check_res


class Problem(ProblemAspect):
    def __init__(self, probdir):
        if probdir[-1] == '/':
            probdir = probdir[:-1]
        self.srcdir = probdir
        self.shortname = os.path.basename(self.srcdir)

    def __enter__(self):
        self._basedir = tempfile.mkdtemp(prefix='testprob', dir='.')
        self.probdir = os.path.join(self._basedir, self.shortname)
        if not os.path.isdir(self.srcdir):
            self.error("Problem directory '%s' not found" % self.srcdir)
            self.shortname = None
            return self

        self.msg('Loading problem %s' % self.shortname)

        shutil.copytree(self.srcdir, self.probdir)

        self.statement = ProblemStatement(self)
        self.config = ProblemConfig(self)
        self.input_format_validators = InputFormatValidators(self)
        self.output_validators = OutputValidators(self)
        self.graders = Graders(self)
        self.testdata = TestCaseGroup(self, os.path.join(self.probdir, 'data'))
        self.submissions = Submissions(self)
        return self

    def __exit__(self, type, value, traceback):
        shutil.rmtree(self._basedir)

    def __str__(self):
        return self.probdir

    def check(self, items='all', bail_on_error=False):
        if self.shortname is None:
            return [1, 0]

        mapping = {'config': self.config,
                   'problem statement': self.statement,
                   'input format validators': self.input_format_validators,
                   'output validators': self.output_validators,
                   'graders': self.graders,
                   'test data': self.testdata,
                   'submissions': self.submissions}
        if items == 'all':
            items = ['config', 'problem statement', 'input format validators', 'output validators', 'test data', 'submissions']

        if not re.match('^[a-z0-9]+$', self.shortname):
            self.error("Invalid shortname '%s' (must be [a-z0-9]+)" % self.shortname)

        ProblemAspect.errors = 0
        ProblemAspect.warnings = 0
        for item in items:
            self.msg('Checking %s' % item)
            mapping[item].check()
            if ProblemAspect.errors > 0 and bail_on_error:
                break
        return [ProblemAspect.errors, ProblemAspect.warnings]


if __name__ == '__main__':
    parser = OptionParser(usage="usage: %prog [options] problems")
    parser.add_option("-l", "--log-level", dest="loglevel", help="set log level (debug, info, warning, error, critical)", default="warning")
#    parser.add_option("-b", dest="bail_on_error", help="bail verification on first error (useful together with debug output)", action=store_true)
#    parser.add_option("-n", "--count", dest="count", help="number of times to run each submission", default=3, type="int")
#    parser.add_option("-g", "--gen-answers", dest="genanswers", help="generate answer files using", default=False)
    (options, args) = parser.parse_args()
    fmt = "%(levelname)s %(message)s"
    logging.basicConfig(stream=sys.stdout,
                        format=fmt,
                        level=eval("logging." + options.loglevel.upper()))

    if not args:
        parser.print_help()

    dirs = [os.path.abspath(a) for a in args]

    for dir in dirs:
        with Problem(dir) as prob:
            [errors, warnings] = prob.check()
            print "%s tested: %d errors, %d warnings" % (dir, errors, warnings)
