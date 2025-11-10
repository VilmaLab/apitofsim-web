create sequence cluster_id_sequence start 1;
create sequence pathway_id_sequence start 1;

create table cluster (
    id integer default nextval('cluster_id_sequence') primary key,
    common_name varchar,
    atomic_mass integer,
    electronic_energy double,
    rotational_temperatures double[3],
    vibrational_temperatures double[],
);

create table pathway (
    id integer default nextval('pathway_id_sequence') primary key,
    cluster_id integer,
    product1_id integer,
    product2_id integer,
    foreign key (cluster_id) references cluster (id),
    foreign key (product1_id) references cluster (id),
    foreign key (product2_id) references cluster (id)
);
