# Toronto Bids Application

There are three components to the "application":

1. A MySQL database containing all of the call data.
2. An API program used to query the database.
3. A frontend that uses API calls to search through the calls.

## MySQL database

Information on setting up the MySQL database is provided in the `db` folder. These instructions will only set you up with a sampling of the complete call data.

## API program

The API program is located in the `server/toronto-bids` folder. It is written in [Clojure](https://clojure.org/guides/install_clojure) and uses the [Leiningen](https://leiningen.org/) build tool.

The `stuff.clj` folder will need to be updated with login information for the MySQL database. Using the username and password of `bids` as shown in `db/README.md`, the db-spec should become this:

```
(str "jdbc:mysql:///" db-name "?user=bids&password=bids")
```

The program can either be compiled to a .jar file using `lein uberjar`, or can be executed immediately with the command `lein run test_bids 8000` (to access the `test_bids` database and respond to API calls on port 8000).

## Frontend

The frontend is located in the `server/tests/frontend` folder, and also uses Clojure and Leiningen.

Either compile the frontend to a .jar using `lein uberjar`, or execute it immediately with the command `lein run http://localhost:8000/api/ 8001 ""`. The first argument points to the running API program, `8001` sets the port to run the frontend on, and the empty quotes set the "local-base".

You can access the running frontend by going to `http://localhost:8001/reset.html` in a web browser.

