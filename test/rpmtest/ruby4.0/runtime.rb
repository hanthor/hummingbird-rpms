require "digest"
require "json"

raise "JSON failed" unless JSON.parse('{"answer":42}')["answer"] == 42
raise "SHA-256 failed" unless Digest::SHA256.hexdigest("rpmtest").length == 64
puts "rpmtest Ruby works: #{19 + 23}"
