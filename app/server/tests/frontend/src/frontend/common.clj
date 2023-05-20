(ns frontend.common
	(:gen-class)
	(:require [clojure.string :as str])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [hiccup.form :as form])
	(:require [hiccup.util :as util])
	(:require [clj-http.client :as client])
)

(def title "Toronto Bids Archives")

(def ALL "*All*")
(def DEFAULT-LIMIT "8")
(def DEFAULT-OFFSET "0")

(defn set-default [default value]
	(if (nil? value) default value)
)

(defn parse-limit [name argument]
	(try
		(when (some? argument) (Integer/parseInt argument))
		(catch NumberFormatException exception 
			(throw (Exception. (str "The " name " argument '" argument "' is not a number")))
		)
	)
)

(defn head [title css-file]
	[:head
		[:title title]
		(page/include-css (str css-file))
	]
)

(defn stay [limit offset]
	offset
)

(defn forward [limit offset]
	(+ offset limit)
)

(defn back [limit offset]
	(max 0 (- offset limit))
)

(defn make-query-params [division type commodity commodity_type buyer posting_date_before posting_date_after closing_date_before closing_date_after search_text limit offset]
	{
		"division" division
		"type" type
		"commodity" commodity
		"commodity_type" commodity_type
		"buyer" buyer
		"posting_date_before" posting_date_before
		"posting_date_after" posting_date_after
		"closing_date_before" closing_date_before
		"closing_date_after" closing_date_after
		"search_text" (if (nil? search_text) "" search_text)
		"limit" (str limit)
		"offset" (str offset)
	}
)

(def default-query-params
	(make-query-params
		ALL ALL ALL ALL ALL
		"" "" "" "" ""
		(parse-limit "limit" DEFAULT-LIMIT)
		(parse-limit "offset" DEFAULT-OFFSET)
	)
)

(defn api-call [api-base endpoint params]
  (->> params
       (util/url (str api-base endpoint))
       (util/to-str)
       (client/get)
       (:body)
       (json/read-str)))
