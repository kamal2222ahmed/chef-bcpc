#!/usr/bin/env python

"""
DNS popper

Asks openstack about all the running instances that currently have floats
then creates a CNAME record to point to the public-X.X.X.X username

"""

import keystoneclient
from keystoneclient.v2_0 import client as kclient
from keystoneclient import exceptions as kc_exceptions
import MySQLdb as mdb
import argparse
import syslog

class dns_popper(object):
   def __init__(self, config):
      self.config = config
      self.keystone = kclient.Client(username= config["OS_USERNAME"], password=config["OS_PASSWORD"],
                                   tenant_name = config["OS_TENANT_NAME"], auth_url=config["OS_AUTH_URL"],
                                   insecure=True)
      dbc = self.config["db"]
      self.db_con = mdb.connect(dbc["host"], dbc["user"], dbc["password"], db=dbc["name"])

      c = self.db_con.cursor()
      c.execute("""select id, name from domains where name=%s""", self.config["domain"])
      rows = c.fetchall()
      if len(rows)!=1:
         syslog.syslog(syslog.LOG_ERROR, "Cannot find unique domain '%s' in pdns DB" % (self.config["domain"]))
         raise Exception("Cannot find unique domain '%s' in pdns DB" % (self.config["domain"]))
      self.domain_id = int(rows[0][0])

   def generate_records_from_vms(self):
      """
      Get all the vms with a float attached
      """
      # The replacements we used to do in SQL
      c = self.db_con.cursor()
      replacements = [("&", "and"),
                      (" ", "-"),
                      ("_", "-"),
                      (".", "-")]

      c.execute("SELECT i.uuid, i.display_name, i.project_id, n.address FROM nova.instances i JOIN nova.fixed_ips f ON i.uuid = f.instance_uuid JOIN nova.floating_ips n ON f.id = n.fixed_ip_id WHERE i.vm_state = 'active' AND i.project_id IS NOT NULL")
      servers = c.fetchall()

      rc = []

      for server in servers:
         project_id = server[2]
         try:
            tenant = self.keystone.tenants.get(project_id)
         except kc_exceptions.NotFound:
            syslog.syslog(syslog.LOG_NOTICE,
               "Non-existent project %s: " % tid +
               "Check that %s is not attached to orphaned instance." % add["addr"])
            continue

         tname = tenant.name
         sname = server[1]
         address = server[3]

         for s, t in replacements:
            tname = tname.replace(s,t)
            sname = sname.replace(s,t)
         dnsname =  str(("%s.%s.%s" %(sname,  tname, self.config["domain"] )).lower())
         rc.append( (dnsname, "CNAME", "public-" + str(address).replace(".", "-") + "."+self.config["domain"]) )

      return rc

   def get_records_from_db(self,):
      c = self.db_con.cursor()
      c.execute("""select name, content from records where type="CNAME" and bcpc_record_type="DYNAMIC" and content like "public-%";""")
      rows = []
      for row in c.fetchall():
         rows.append( (row[0],  "CNAME", row[1] ))
      return rows

   def update_db(self, db_rows, nova_rows):
      ds = set( db_rows )
      ns = set( nova_rows )
      to_delete = ds - ns
      to_add = ns - ds
      c = self.db_con.cursor()
      try:
         if to_delete:
            syslog.syslog(syslog.LOG_NOTICE, "Deleting %d CNAMEs from pdns" % (len(to_delete)))
            c.executemany("""delete from records where name=%s and type=%s and content=%s and bcpc_record_type='DYNAMIC'""", to_delete)
         if to_add:
            syslog.syslog(syslog.LOG_NOTICE, "Adding %d CNAMEs to pdns" % (len(to_add)))
            c.executemany("""insert into records  (domain_id, name, type, content, ttl, bcpc_record_type) values (%s, %s, %s, %s, 300, 'DYNAMIC')""",
                          [(self.domain_id, rec[0], rec[1], rec[2]) for rec in to_add] )
         self.db_con.commit()
      except  mdb.Error, e:
         self.db_cnn.rollback()
         syslog.syslog(syslog.LOG_ERROR, "DB changes failed: %d: %s" % (e.args[0],e.args[1]))


def c_load_config(path):
   import yaml
   return yaml.load(open(path))

def c_run(args):
   config = c_load_config(args.config)
   dnsp = dns_popper(config)
   nova_rows = dnsp.generate_records_from_vms()
   db_rows = dnsp.get_records_from_db()
   dnsp.update_db(db_rows, nova_rows)


def c_dump(args):
   config = c_load_config(args.config)
   dnsp = dns_popper(config)
   nrec = dnsp.generate_records_from_vms()
   dbrec = dnsp.get_records_from_db()
   print nrec, dbrec

if __name__ == '__main__':
   import sys
   parser = argparse.ArgumentParser()
   parser.add_argument('-c', '--config', dest="config", default="config.yml", help='Config file')
   subparsers = parser.add_subparsers(help="commands")
   parser_run = subparsers.add_parser('run', help='Sync DNS DB with nova state')
   parser_run.set_defaults(func=c_run)
   parser_dump = subparsers.add_parser('dump', help='dump current state')
   parser_dump.set_defaults(func=c_dump)
   args = parser.parse_args()
   args.func(args)
   sys.exit(0)
