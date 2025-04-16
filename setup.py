from setuptools import setup

setup(
   name='url_benchmark',
   version='1.0',
   packages=['url_benchmark', 'url_benchmark.agent', 'url_benchmark.custom_dmc_tasks', 'url_benchmark.gridworld'], 
   package_data={'url_benchmark': ['*.yaml'], 'url_benchmark.agent': ['*.yaml'], 'url_benchmark.custom_dmc_tasks': ['*.xml']},
   include_package_data=True,
)