# Database setup instructions

## Database installation

The server's database should run on a MySQL-compatible database server. MySQL
and MariaDB are suitable options.

[MariaDB installation](https://mariadb.com/kb/en/binary-packages/)  
[MySQL installation](https://dev.mysql.com/downloads/mysql/)  

## Setup instructions

Start up the server. You should be able to log in to the server with some
root/administrator account and get access to a command prompt.

Create the necessary databases:

```
create database import;
create database test_bids;
```

Create a new user to access these databases. The below example creates user
`bids` with password `bids`.

**This is insecure!** but should be okay for local testing. 

```
create user 'bids'@'localhost' identified by 'bids';
grant all privileges on import.* to 'bids'@'localhost';
grant all privileges on test_bids.* to 'bids'@'localhost';
```

Type `quit` to exit the MySQL client.

Finally, execute the SQL files in this directory to populate the databases.

1. Use `fromxml.sql` on database `import`.
2. Use `schema.sql` on database `test_bids`.
3. Use `make_test.sql` on database `test_bids`.

From a command prompt with access to the `mysql` client, the following commands
should work:

```
mysql -u bids -p bids import < fromxml.sql
mysql -u bids -p bids test_bids < schema.sql
mysql -u bids -p bids test_bids < make_test.sql
```

