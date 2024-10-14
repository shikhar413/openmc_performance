import statistics
import datetime


class BenchmarkResult:
    def __init__(self, results, name):
        self._results = results
        self._name = name
        self._result_date = datetime.datetime.now(datetime.UTC)
        self._data = {
            'benchmark': name,
            'results_value': self.mean,
            'std_dev': self.std_dev,
            'min': self.min,
            'max': self.max,
            'result_date': self.result_date
        }
        self._revision_date = None
        self._commitid = None
        self._version = None
        self._branch = None
        self._project = None
        self._environment = None

    @property
    def name(self):
        return self._name

    @property
    def results(self):
        return self._results

    @property
    def data(self):
        return self._data

    @property
    def result_date(self):
        return self._result_date

    @property
    def n_trials(self):
        return len(self.results)

    @property
    def min(self):
        return min(self.results)

    @property
    def max(self):
        return max(self.results)

    @property
    def mean(self):
        return statistics.mean(self.results)

    @property
    def std_dev(self):
        return statistics.stdev(self.results) if self.n_trials > 1 else None

    @property
    def revision_date(self):
        return self._revision_date

    @property
    def commitid(self):
        return self._commitid

    @property
    def version(self):
        return self._version

    @property
    def branch(self):
        return self._branch

    @property
    def project(self):
        return self._project

    @property
    def environment(self):
        return self._environment

    def add_executable_info(self, executable_info, environment_info, branch, project):
        #TODO-SK add inactive/active calc rates, mpi/omp params to executable name
        commitid, date, version = executable_info
        self.data.update({
            'revision_date': date,
            'commitid': commitid,
            'executable': version,
            'branch': branch,
            'project': project,
            'environment': environment_info
        })
        self._revision_date = date
        self._commitid = commitid
        self._version = version
        self._branch = branch
        self._project = project
        self._environment = environment_info
