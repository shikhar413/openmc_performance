import statistics
import datetime


class BenchmarkResult:
    def __init__(self, name=None, results=None, json_data=None):
        if results:
            # TODO-SK make sure name is not None
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
        else:
            # Data should come from json-formatted dict object
            # TODO-SK add checks to make sure json_data is not None
            # TODO-SK retrieve dict data more safely

            self._data = json_data
            self._name = json_data['benchmark']
            # Input json data stores dates as strings, convert to datetime object
            self._result_date = datetime.datetime.fromisoformat(json_data['result_date'])
            self._revision_date = datetime.datetime.fromisoformat(json_data['revision_date'])
            # Update json data dates to datetime objects as well
            self._data['result_date'] = self._result_date
            self._data['revision_date'] = self._revision_date
            self._commitid = json_data['commitid']
            self._version = json_data['executable']
            self._branch = json_data['branch']
            self._project = json_data['project']
            self._environment = json_data['environment']
            self._results = None

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
        return len(self.results) if self.results else None

    @property
    def min(self):
        return min(self.results) if self.results else self.data['min']

    @property
    def max(self):
        return max(self.results) if self.results else self.data['max']

    @property
    def mean(self):
        return statistics.mean(self.results) if self.results else self.data['results_value']

    @property
    def std_dev(self):
        if self._results:
            return statistics.stdev(self.results) if self.n_trials > 1 else None
        else:
            return self.data['std_dev']

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
