import re
import MySQLdb
from collections import namedtuple
from math import ceil

QueryInfo = namedtuple('QueryInfo', ['page', 'order_by', 'filter'], verbose=False)

class ResultOrder:
    def __init__(self, column_orders=None):
        self.order_by = []
        if column_orders:
            for x in column_orders:
                self.add(x)

    def addRaw(self, column, direction):
        self.add(ColumnOrder(column, direction))

    def add(self, column_order):
        if not isinstance(column_order, ColumnOrder):
            raise TypeError("ResultOrder expects a ColumnOrder object")
        self.order_by.append(column_order)

    def to_clause(self):
        if len(self.order_by)==0:
            return ""
        return "ORDER BY " + ", ".join((x.to_clause() for x in self.order_by))

class ColumnOrder:
    def __init__(self, column, direction="ASC"):
        self.column = column
        self.direction = direction

    def to_clause(self):
        return "{} {}".format(self.column, self.direction)

    def __repr__(self):
        return "<ColumnOrder {},{}>".format(self.column, self.direction)

class TableJoin:
    def __init__(self, table, on, join_type="LEFT"):
        self.table = table
        self.on = on
        self.join_type = "LEFT"

    def to_clause(self):
        return "{} JOIN {} ON {}".format(self.join_type, self.table, self.on)


class WhereExpression:
    def __init__(self, where="", vals=()):
        self.where = where
        self.vals = vals

    def to_clause(self):
        return self.where, self.vals

class WhereGroup:
    def __init__(self, *exprs):
        self.__join_verb = "??"
        self.wheres = []
        for expr in exprs:
            self.add(expr)

    def add(self, expr):
        self.wheres.append(expr)

    def to_clause(self):
        if len(self.wheres) <1 :
            return None, None
        elif len(self.wheres) == 1:
            return self.wheres[0].to_clause()
        where = []
        vals = ()
        for expr in self.wheres:
            w, v = expr.to_clause()
            where.append(w)
            vals = vals + v
        connect = ") " + self.__join_verb + " ("
        wheres = "(" + connect.join(where) + ")"
        return wheres, vals

class WhereAll(WhereGroup):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__join_verb = "AND"

class WhereAny(WhereGroup):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__join_verb = "OR"

class WhereClause(WhereAll):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def to_clause(self):
        w, v = super().to_clause()
        if not w:
            return ""
        return "WHERE " + w, v

class SelectQuery:
    def __init__(self):
        self.cols = []
        self.table = ""
        self.joins = []
        self.where = None
        self.order = None
        self.page = None

    @staticmethod
    def __base_sql(cols=[], table="", joins=[], where=None, order=None, page=None):
        vals = ()
        sql = "SELECT "
        sql += ", ".join(cols)
        sql += " FROM " + table
        for join in joins:
            sql += " " + join.to_clause()
        if where:
            w, v =  where.to_clause()
            sql += " " + w
            vals += v
        if order:
            sql += " " + order.to_clause()
        if page:
            sql += " LIMIT %s OFFSET %s"
            vals += (page.limit, page.offset)
        return sql, vals

    def cmd_select(self):
        sql, vals = SelectQuery.__base_sql(self.cols, self.table, self.joins,
            self.where, self.order, self.page)
        return sql, vals

    def cmd_count(self):
        sql, vals = SelectQuery.__base_sql(["count(*) as count"], self.table, self.joins,
            self.where)
        return sql, vals

    def set_cols(self, cols):
        self.cols = cols
        return self

    def add_col(self, col):
        self.cols.append(col)
        return self

    def set_table(self, table):
        self.table = table
        return self

    def set_joins(self, joins):
        self.joins = joins
        return self

    def add_join(self, join):
        self.joins.append(join)
        return self

    def set_where(self, where):
        self.where = where
        return self

    def set_order_by(self, order):
        self.order = order
        return self

    def set_page(self, page):
        self.page = page
        return self


PageInfo = namedtuple('PageInfo', ['limit', 'offset'], verbose=False)

class PagedResult:
    def __init__(self, results, total_count=0, page=None):
        self.results = results
        self.page = page
        self.total_count = total_count

    def next_page(self):
        if self.page is None:
            return None
        if self.page.offset + self.page.limit >= self.total_count:
            return None
        return PageInfo(self.page.limit, self.page.offset + self.page.limit)

    def prev_page(self):
        if self.page is None:
            return None
        if self.page.offset == 0:
            return None
        return PageInfo(self.page.limit, min(self.page.offset-self.page.limit, 0))

    def page_count(self):
        if self.page is None:
            if self.total_count>0:
                return 1
            else:
                return 0
        return int(ceil(self.total_count / float(self.page.limit)))

    @staticmethod
    def execute_select(db, sqlcmd):
        cur = db.cursor(MySQLdb.cursors.DictCursor)
        sql, vals =  sqlcmd.cmd_select()
        page = sqlcmd.page
        cur.execute(sql, vals)
        results = cur.fetchall()
        if page and (page.offset>0 or len(results)==page.limit):
            sql, vals = sqlcmd.cmd_count()
            cur.execute(sql, vals)
            total_count = cur.fetchone()["count"]
        else:
            total_count = len(results)
        return PagedResult(results, total_count, page)