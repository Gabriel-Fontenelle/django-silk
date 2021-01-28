import logging
import traceback

from django.core.exceptions import EmptyResultSet
from django.db import connection
from django.utils import timezone
from django.db import router

from silk.collector import DataCollector
from silk.config import SilkyConfig

Logger = logging.getLogger('silk.sql')


def _should_wrap(sql_query):
    if not DataCollector().request:
        return False

    for ignore_str in SilkyConfig().SILKY_IGNORE_QUERIES:
        if ignore_str in sql_query:
            return False
    return True

def _unpack_explanation(result):
     for row in result:
         if not isinstance(row, str):
             yield ' '.join(str(c) for c in row)
         else:
             yield row

def _explain_query(q, params):
    if connection.features.supports_explaining_query_execution:
        if SilkyConfig().SILKY_ANALYZE_QUERIES:
            prefix = connection.ops.explain_query_prefix(
                analyze = True
            )
        else:
            prefix = connection.ops.explain_query_prefix()

        # currently we cannot use explain() method
        # for queries other than `select`
        prefixed_query = "{} {}".format(prefix, q)
        with connection.cursor() as cur:
            cur.execute(prefixed_query, params)
            result = _unpack_explanation(cur.fetchall())
            return '\n'.join(result)
    return None


def execute_sql(self, *args, **kwargs):
    """wrapper around real execute_sql in order to extract information"""

    try:
        q, params = self.as_sql()
        if not q:
            raise EmptyResultSet
    except EmptyResultSet:
        try:
            result_type = args[0]
        except IndexError:
            result_type = kwargs.get('result_type', 'multi')
        if result_type == 'multi':
            return iter([])
        else:
            return
    tb = ''.join(reversed(traceback.format_stack()))
    sql_query = q % params

    if 'INSERT' in sql_query or 'UPDATE' in sql_query or 'DELETE' in sql_query:
        db = router.db_for_write(self.query.model)
    else:
        db = router.db_for_read(self.query.model)

    if _should_wrap(sql_query):
        query_dict = {
            'database': db,
            'model': self.query.model,
            'query': sql_query,
            'start_time': timezone.now(),
            'traceback': tb
        }
        try:
            return self._execute_sql(*args, **kwargs)
        finally:
            query_dict['end_time'] = timezone.now()
            request = DataCollector().request
            if request:
                query_dict['request'] = request
            if self.query.model.__module__ != 'silk.models':
                query_dict['analysis'] = _explain_query(q, params)
                DataCollector().register_query(query_dict)
            else:
                DataCollector().register_silk_query(query_dict)
    return self._execute_sql(*args, **kwargs)
